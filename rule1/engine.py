"""Phil Town's Rule #1 math: Big 5 numbers, sticker price, margin of safety,
payback time and debt check. Pure functions, no network access."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

WINDOWS = (10, 5, 3, 1)
BIG5 = ("roic", "sales", "eps", "equity", "cash")
BIG5_LABELS = {
    "roic": "ROIC",
    "sales": "Sales growth",
    "eps": "EPS growth",
    "equity": "Equity (BVPS) growth",
    "cash": "Operating cash flow growth",
}


@dataclass
class Year:
    """One fiscal year of fundamentals. Money in the reporting currency."""
    year: int
    revenue: float | None = None
    eps: float | None = None
    net_income: float | None = None
    ocf: float | None = None          # operating cash flow
    capex: float | None = None        # positive number
    equity: float | None = None
    shares: float | None = None       # diluted, split-adjusted
    total_debt: float | None = None

    @property
    def fcf(self) -> float | None:
        if self.ocf is None:
            return None
        return self.ocf - (self.capex or 0.0)

    @property
    def implied_shares(self) -> float | None:
        if self.shares:
            return self.shares
        # Some filers (e.g. dual-class companies) omit a combined share count; derive it.
        if self.net_income and self.eps and self.net_income > 0 and self.eps > 0:
            return self.net_income / self.eps
        return None

    @property
    def bvps(self) -> float | None:
        shares = self.implied_shares
        if self.equity is None or not shares:
            return None
        return self.equity / shares

    @property
    def roic(self) -> float | None:
        # Phil Town's simple ROIC: net income / (equity + debt)
        if self.net_income is None or self.equity is None:
            return None
        capital = self.equity + (self.total_debt or 0.0)
        if capital <= 0:
            return None
        return self.net_income / capital


def cagr(start: float | None, end: float | None, years: int) -> float | None:
    """Compound annual growth rate. None when it is not meaningful (negative/zero start or end)."""
    if start is None or end is None or years <= 0 or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def _series(years: list[Year], attr: str) -> list[float | None]:
    return [getattr(y, attr) for y in years]


@dataclass
class MetricResult:
    windows: dict[int, float | None] = field(default_factory=dict)
    passed: bool | None = None  # None = not enough data

    def to_dict(self) -> dict:
        return {"windows": {str(k): v for k, v in self.windows.items()}, "passed": self.passed}


def big5(years: list[Year], min_growth: float = 0.10) -> dict:
    """Compute the Big 5 over 10/5/3/1-year windows (as far back as data allows).

    A metric passes when every available window is >= min_growth.
    A window with a negative start/end (growth not computable) counts as a fail.
    """
    years = sorted(years, key=lambda y: y.year)
    n = len(years)
    growth_attrs = {"sales": "revenue", "eps": "eps", "equity": "bvps", "cash": "ocf"}
    results: dict[str, MetricResult] = {}

    for key, attr in growth_attrs.items():
        values = _series(years, attr)
        # Equity per share needs a share count; where it is missing use total equity.
        fallback = _series(years, "equity") if key == "equity" else None
        res = MetricResult()
        spans = [w for w in WINDOWS if n - 1 - w >= 0]
        if n >= 3 and (n - 1) not in WINDOWS and n - 1 < 10:
            spans.insert(0, n - 1)  # history shorter than 10 years: add the longest window
        for w in spans:
            start, end = values[n - 1 - w], values[n - 1]
            if (start is None or end is None) and fallback:
                start, end = fallback[n - 1 - w], fallback[n - 1]
            if start is None or end is None:
                continue
            res.windows[w] = cagr(start, end, w)
        if res.windows:
            res.passed = all(v is not None and v >= min_growth for v in res.windows.values())
        results[key] = res

    # ROIC: average over each window (last w years), plus the latest year.
    roics = _series(years, "roic")
    res = MetricResult()
    roic_windows = [w for w in WINDOWS if n >= w]
    if 1 < n < 10 and n not in WINDOWS:
        roic_windows.append(n)  # longest available history
    for w in sorted(roic_windows, reverse=True):
        chunk = roics[n - w:]
        known = [r for r in chunk if r is not None]
        # A missing year counts as unknown; a window needs at least half its years.
        res.windows[w] = sum(known) / len(known) if len(known) * 2 >= w else None
    if res.windows:
        res.passed = all(v is not None and v >= min_growth for v in res.windows.values())
    results["roic"] = res

    checks = [v for r in results.values() for v in r.windows.values()]
    passed_checks = sum(1 for v in checks if v is not None and v >= min_growth)
    return {
        "metrics": {k: results[k].to_dict() for k in BIG5},
        "score": passed_checks / len(checks) if checks else 0.0,
        "all_pass": all(results[k].passed for k in BIG5),
        "years_of_data": n,
    }


def historical_growth(years: list[Year]) -> float | None:
    """Longest-window equity (BVPS) growth, Rule #1's default growth estimate."""
    years = sorted(years, key=lambda y: y.year)
    best = None
    for attr in ("bvps", "equity"):
        vals = [(y.year, getattr(y, attr)) for y in years if getattr(y, attr) is not None]
        if len(vals) >= 2:
            (y0, v0), (y1, v1) = vals[0], vals[-1]
            # prefer per-share data unless total equity covers a clearly longer span
            if best is None or (y1 - y0) > best[0] + 2:
                best = (y1 - y0, cagr(v0, v1, y1 - y0))
    return best[1] if best else None


def choose_growth(hist: float | None, analyst: float | None, cap: float) -> float | None:
    """Rule #1: use the lower of historical equity growth and analyst estimate, capped."""
    candidates = [g for g in (hist, analyst) if g is not None and not math.isnan(g)]
    if not candidates:
        return None
    g = min(min(candidates), cap)
    return g if g > 0 else None


def sticker_price(eps: float | None, growth: float | None, hist_pe: float | None,
                  marr: float = 0.15, years: int = 10, pe_cap: float = 50) -> dict | None:
    """Sticker price = value today of the price in `years`, discounted at MARR."""
    if eps is None or growth is None or eps <= 0 or growth <= 0:
        return None
    future_pe = 2 * growth * 100
    if hist_pe is not None and hist_pe > 0:
        future_pe = min(future_pe, hist_pe)
    future_pe = min(future_pe, pe_cap)
    future_eps = eps * (1 + growth) ** years
    future_price = future_eps * future_pe
    sticker = future_price / (1 + marr) ** years
    return {
        "growth": growth,
        "future_eps": future_eps,
        "future_pe": future_pe,
        "future_price": future_price,
        "sticker": sticker,
    }


def mos_price(sticker: float | None, margin_of_safety: float) -> float | None:
    return None if sticker is None else sticker * (1 - margin_of_safety)


def payback_years(market_cap: float | None, fcf: float | None, growth: float | None,
                  max_years: int = 30) -> int | None:
    """Years of growing free cash flow needed to add up to the market cap.
    Returns max_years + 1 if it never pays back in that horizon."""
    if not market_cap or fcf is None or fcf <= 0 or growth is None:
        return None
    total, cash = 0.0, fcf
    for year in range(1, max_years + 1):
        cash *= 1 + growth
        total += cash
        if total >= market_cap:
            return year
    return max_years + 1


def debt_payoff_years(total_debt: float | None, fcf: float | None) -> float | None:
    if total_debt is None:
        return None
    if total_debt <= 0:
        return 0.0
    if fcf is None or fcf <= 0:
        return math.inf
    return total_debt / fcf
