"""SEC EDGAR: 10+ years of annual fundamentals and 8-K event filings for US companies.
Free, official, no API key. SEC asks for a descriptive User-Agent and <= 10 requests/second."""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta

import requests

from .engine import Year

UA = {"User-Agent": os.environ.get("SEC_USER_AGENT", "rule1-screener research-bot contact@example.com")}
_last_call = 0.0
_ticker_map: dict[str, int] | None = None


def _get(url: str) -> dict:
    global _last_call
    wait = 0.15 - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()


def cik_for(ticker: str) -> int | None:
    global _ticker_map
    if _ticker_map is None:
        data = _get("https://www.sec.gov/files/company_tickers.json")
        _ticker_map = {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}
    return _ticker_map.get(ticker.upper().replace("-", "."), _ticker_map.get(ticker.upper()))


# Concepts in priority order; the first one with a value for a year wins.
DURATION = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
}
INSTANT = {
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "debt_total": ["LongTermDebt", "DebtLongtermAndShorttermCombinedAmount"],
    "debt_noncurrent": ["LongTermDebtNoncurrent"],
    "debt_current": ["LongTermDebtCurrent", "DebtCurrent"],
    "short_borrow": ["ShortTermBorrowings", "CommercialPaper"],
}


def _annual_facts(gaap: dict, concepts: list[str], duration: bool) -> dict[str, tuple[float, str]]:
    """Return {period_end: (value, filed_date)} from annual reports.

    For each period end, prefer the highest-priority concept; within a concept, the most
    recently filed value (later reports restate earlier years)."""
    best: dict[str, tuple[int, str, float]] = {}  # end -> (rank, filed, value)
    for rank, concept in enumerate(concepts):
        for unit_vals in gaap.get(concept, {}).get("units", {}).values():
            for f in unit_vals:
                if not str(f.get("form", "")).startswith(("10-K", "20-F", "40-F")):
                    continue
                if duration:
                    if "start" not in f:
                        continue
                    days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days
                    if not 340 <= days <= 380:
                        continue
                end, filed = f["end"], f["filed"]
                prev = best.get(end)
                if prev is None or rank < prev[0] or (rank == prev[0] and filed > prev[1]):
                    best[end] = (rank, filed, float(f["val"]))
    return {end: (v[2], v[1]) for end, v in best.items()}


def _split_factor(filed: str, splits: list[tuple[str, float]]) -> float:
    """Product of all split ratios that happened after this value was filed."""
    factor = 1.0
    for split_date, ratio in splits:
        if split_date > filed:
            factor *= ratio
    return factor


def annual_fundamentals(ticker: str, splits: list[tuple[str, float]] | None = None,
                        max_years: int = 11) -> list[Year]:
    """Annual history from EDGAR. `splits` = [(YYYY-MM-DD, ratio)] to adjust per-share data,
    because older filings report EPS and share counts before later stock splits."""
    cik = cik_for(ticker)
    if cik is None:
        return []
    facts = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    gaap = facts.get("facts", {}).get("us-gaap", {})
    splits = splits or []

    by_end: dict[str, dict] = {}
    for field, concepts in DURATION.items():
        for end, (val, filed) in _annual_facts(gaap, concepts, True).items():
            if field == "eps":
                val /= _split_factor(filed, splits)
            elif field == "shares":
                val *= _split_factor(filed, splits)
            by_end.setdefault(end, {})[field] = val
    for field, concepts in INSTANT.items():
        for end, (val, _filed) in _annual_facts(gaap, concepts, False).items():
            if end in by_end:  # only balance sheets that line up with a fiscal year end
                by_end[end][field] = val

    years: dict[int, Year] = {}
    for end in sorted(by_end):
        d = by_end[end]
        if "revenue" not in d and "net_income" not in d:
            continue
        debt = d.get("debt_total")
        if debt is None and ("debt_noncurrent" in d or "debt_current" in d):
            debt = d.get("debt_noncurrent", 0.0) + d.get("debt_current", 0.0)
        if debt is not None:
            debt += d.get("short_borrow", 0.0)
        fy = (date.fromisoformat(end) - timedelta(days=7)).year  # 52/53-week years ending early Jan
        years[fy] = Year(year=fy, revenue=d.get("revenue"), eps=d.get("eps"),
                         net_income=d.get("net_income"), ocf=d.get("ocf"), capex=d.get("capex"),
                         equity=d.get("equity"), shares=d.get("shares"), total_debt=debt)
    return [years[y] for y in sorted(years)][-max_years:]


# 8-K items that matter for Rule #1 "Management" and risk.
RED_ITEMS = {
    "5.02": ("high", "Director or executive officer departure/appointment"),
    "4.02": ("high", "Previously issued financial statements can no longer be relied on"),
    "4.01": ("medium", "Change of auditor"),
    "1.03": ("high", "Bankruptcy or receivership"),
    "2.06": ("medium", "Material impairment"),
    "3.01": ("high", "Delisting notice or failure to meet listing rules"),
    "2.04": ("medium", "Event that accelerates a debt obligation"),
    "1.05": ("medium", "Material cybersecurity incident"),
}


def recent_8k_events(ticker: str, lookback_days: int = 14) -> list[dict]:
    cik = cik_for(ticker)
    if cik is None:
        return []
    sub = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    recent = sub["filings"]["recent"]
    cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).date().isoformat()
    events = []
    for i, form in enumerate(recent["form"]):
        filed = recent["filingDate"][i]
        if filed < cutoff:
            break
        if form not in ("8-K", "8-K/A"):
            continue
        for item in str(recent["items"][i]).split(","):
            item = item.strip()
            if item in RED_ITEMS:
                severity, label = RED_ITEMS[item]
                acc = recent["accessionNumber"][i].replace("-", "")
                doc = recent["primaryDocument"][i]
                events.append({
                    "title": f"SEC 8-K item {item}: {label}",
                    "link": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}",
                    "published": filed,
                    "source": "SEC EDGAR",
                    "severity": severity,
                    "category": "management" if item == "5.02" else "filing",
                })
    return events
