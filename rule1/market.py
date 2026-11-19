"""Yahoo Finance (via yfinance): prices, company profile, European fundamentals, FX.
Free and unofficial: it can rate-limit or change, so every call is defensive."""

from __future__ import annotations

import logging
import math

import pandas as pd
import yfinance as yf

from .engine import Year

log = logging.getLogger(__name__)

# Some exchanges quote in minor units (pence, cents). Convert to the major currency.
MINOR_UNITS = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}


def normalize_currency(ccy: str | None) -> tuple[str | None, float]:
    """Return (major currency, divisor to apply to prices)."""
    if ccy in MINOR_UNITS:
        return MINOR_UNITS[ccy]
    return ccy, 1.0


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _row(df: pd.DataFrame, *names: str) -> pd.Series | None:
    for n in names:
        if df is not None and n in df.index:
            return df.loc[n]
    return None


def profile(t: yf.Ticker) -> dict:
    info = t.info or {}
    ccy, div = normalize_currency(info.get("currency"))
    fin_ccy, _ = normalize_currency(info.get("financialCurrency") or info.get("currency"))
    price = _num(info.get("currentPrice") or info.get("regularMarketPrice"))
    return {
        "name": info.get("longName") or info.get("shortName"),
        "long_name": info.get("longName"),
        "sector": info.get("sector") or "Unknown",
        "industry": info.get("industry") or "Unknown",
        "country": info.get("country"),
        "currency": ccy,
        "price_divisor": div,
        "financial_currency": fin_ccy,
        "price": price / div if price else None,
        "shares_outstanding": _num(info.get("sharesOutstanding")),
        "summary": (info.get("longBusinessSummary") or "")[:1200],
        "website": info.get("website"),
    }


def splits(t: yf.Ticker) -> list[tuple[str, float]]:
    try:
        s = t.splits
        return [(d.strftime("%Y-%m-%d"), float(r)) for d, r in s.items() if r and r > 0]
    except Exception:
        return []


def annual_fundamentals(t: yf.Ticker) -> list[Year]:
    """Annual history from Yahoo (usually only 4-5 years). Used for Europe and as a US fallback."""
    inc, cf, bs = t.financials, t.cashflow, t.balance_sheet
    if inc is None or inc.empty:
        return []
    rows = {
        "revenue": _row(inc, "Total Revenue", "Operating Revenue"),
        "eps": _row(inc, "Diluted EPS", "Basic EPS"),
        "net_income": _row(inc, "Net Income Common Stockholders", "Net Income"),
        "shares": _row(inc, "Diluted Average Shares", "Basic Average Shares"),
        "ocf": _row(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
        "capex": _row(cf, "Capital Expenditure"),
        "equity": _row(bs, "Stockholders Equity", "Common Stock Equity"),
        "total_debt": _row(bs, "Total Debt"),
    }
    years: dict[int, Year] = {}
    for col in inc.columns:
        y = Year(year=col.year)
        for field, series in rows.items():
            if series is None:
                continue
            # cash flow / balance sheet columns can be missing for a year
            val = _num(series.get(col)) if col in series.index else None
            if field == "capex" and val is not None:
                val = abs(val)
            setattr(y, field, val)
        if y.revenue is not None or y.net_income is not None:
            years[y.year] = y
    return [years[k] for k in sorted(years)]


def ttm(t: yf.Ticker) -> dict:
    """Trailing-twelve-month EPS and free cash flow (in the financial currency)."""
    out = {"eps": None, "fcf": None, "eps_one_off": False}
    try:
        q = t.quarterly_financials
        eps = _row(q, "Diluted EPS", "Basic EPS")
        if eps is not None and eps.iloc[:4].notna().sum() == 4:
            out["eps"], out["eps_one_off"] = smooth_ttm([float(x) for x in eps.iloc[:4]])
    except Exception as e:
        log.debug("quarterly income failed: %s", e)
    try:
        qcf = t.quarterly_cashflow
        ocf = _row(qcf, "Operating Cash Flow")
        capex = _row(qcf, "Capital Expenditure")
        if ocf is not None and ocf.iloc[:4].notna().sum() == 4:
            c = capex.iloc[:4].fillna(0).abs().sum() if capex is not None else 0.0
            out["fcf"] = float(ocf.iloc[:4].sum() - c)
    except Exception as e:
        log.debug("quarterly cash flow failed: %s", e)
    return out


def smooth_ttm(quarters: list[float]) -> tuple[float, bool]:
    """Sum 4 quarters of EPS, replacing a one-off spike (> 2x the median of the other
    three, e.g. a big investment gain) with that median. Returns (eps, was_adjusted)."""
    q = list(quarters)
    for i, v in enumerate(q):
        others = sorted(q[:i] + q[i + 1:])
        med = others[1]
        if med > 0 and v > 2 * med:
            q[i] = med
            return sum(q), True
    return sum(q), False


def analyst_growth(t: yf.Ticker) -> float | None:
    """Analysts' long-term (5-year) EPS growth estimate, when Yahoo has one."""
    try:
        ge = t.growth_estimates
        if ge is not None and "LTG" in ge.index:
            return _num(ge.loc["LTG", "stockTrend"])
    except Exception:
        pass
    return None


def historical_pe(t: yf.Ticker, years: list[Year], price_divisor: float, fx: float) -> float | None:
    """Median of each fiscal year's average price / that year's EPS (positive EPS only).
    fx converts EPS (financial currency) into the price currency."""
    try:
        hist = t.history(period="11y", interval="1mo", auto_adjust=False)["Close"] / price_divisor
    except Exception:
        return None
    if hist.empty:
        return None
    pes = []
    for y in years:
        if not y.eps or y.eps <= 0:
            continue
        in_year = hist[hist.index.year == y.year]
        if len(in_year) >= 6:
            pes.append(float(in_year.mean()) / (y.eps * fx))
    if len(pes) < 3:
        return None
    pes.sort()
    return pes[len(pes) // 2]


_fx_cache: dict[tuple[str, str], float] = {}


def fx_rate(src: str | None, dst: str | None) -> float:
    """How many `dst` per 1 `src`."""
    if not src or not dst or src == dst:
        return 1.0
    key = (src, dst)
    if key not in _fx_cache:
        rate = _fx_quote(src, dst)
        if rate is None:
            inverse = _fx_quote(dst, src)
            rate = 1 / inverse if inverse else None
        if rate is None and "USD" not in (src, dst):
            rate = fx_rate(src, "USD") * fx_rate("USD", dst)
        if rate is None:
            log.warning("FX %s->%s unavailable; using 1.0", src, dst)
            rate = 1.0
        _fx_cache[key] = rate
    return _fx_cache[key]


def _fx_quote(src: str, dst: str) -> float | None:
    try:
        h = yf.Ticker(f"{src}{dst}=X").history(period="5d")
        return float(h["Close"].iloc[-1]) if not h.empty else None
    except Exception:
        return None


def daily_history(tickers: list[str], period: str = "9mo") -> dict[str, pd.DataFrame]:
    """Download daily OHLC for many tickers in batches (one request per batch)."""
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), 100):
        batch = tickers[i:i + 100]
        try:
            df = yf.download(batch, period=period, interval="1d", group_by="ticker",
                             auto_adjust=True, progress=False, threads=True)
        except Exception as e:
            log.warning("price batch %d failed: %s", i, e)
            continue
        for tk in batch:
            try:
                sub = df[tk] if len(batch) > 1 else df
                if isinstance(sub.columns, pd.MultiIndex):
                    sub = sub.droplevel(0, axis=1)
                sub = sub.dropna(subset=["Close"])
                if not sub.empty:
                    out[tk] = sub[["High", "Low", "Close"]]
            except KeyError:
                continue
    return out
