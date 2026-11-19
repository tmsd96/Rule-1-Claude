"""Command-line scanner, run on a schedule by GitHub Actions.

    python -m rule1.scanner fundamentals   # slow, weekly: 10y financials for every stock
    python -m rule1.scanner prices         # fast, several times a day: prices, tools, news, alerts
    python -m rule1.scanner one NVDA       # analyse a single stock and print it
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import yfinance as yf

from . import alerts, engine, market, news, sec
from .config import DATA_DIR, load_config
from .technicals import tools_signal
from .universe import build_universe

log = logging.getLogger("rule1")

FUND_FILE = DATA_DIR / "fundamentals.json"
RESULTS_FILE = DATA_DIR / "results.json"
NEWS_FILE = DATA_DIR / "news.json"
PRICES_FILE = DATA_DIR / "prices.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"), default=str)
    tmp.replace(path)


# ---------------------------------------------------------------- fundamentals

def fetch_fundamentals(ticker: str, region: str) -> dict:
    t = yf.Ticker(ticker)
    prof = market.profile(t)
    rec = {**prof, "ticker": ticker, "region": region, "updated": now_iso()}
    years: list[engine.Year] = []
    source = None
    if region == "US":
        try:
            years = sec.annual_fundamentals(ticker, market.splits(t))
            source = "SEC EDGAR"
        except sec.SECBlocked as e:
            log.warning("%s: %s Falling back to Yahoo.", ticker, e)
        except Exception as e:
            log.warning("%s: EDGAR failed (%s), falling back to Yahoo", ticker, e)
    if len(years) < 5:
        yf_years = market.annual_fundamentals(t)
        if len(yf_years) > len(years):
            years, source = yf_years, "Yahoo Finance"
    rec["source"] = source
    rec["years"] = [asdict(y) for y in years]
    rec.update({f"ttm_{k}": v for k, v in market.ttm(t).items()})
    rec["analyst_growth"] = market.analyst_growth(t)
    fx = market.fx_rate(prof["financial_currency"], prof["currency"])
    rec["fx_fin_to_px"] = fx
    rec["hist_pe"] = market.historical_pe(t, years, prof["price_divisor"], fx) if years else None
    return rec


def run_fundamentals(cfg: dict, max_age_days: int = 6, limit: int | None = None) -> None:
    universe = build_universe(cfg["universe"]["indices"])
    save_json(DATA_DIR / "universe.json", universe.to_dict(orient="records"))
    store = load_json(FUND_FILE, {})
    fresh_cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    def needs_refresh(r: dict) -> bool:
        rec = store.get(r["ticker"], {})
        if r["region"] == "US" and rec.get("source") != "SEC EDGAR":
            return True  # retry SEC (10+ years) for US stocks that only got Yahoo data
        return rec.get("updated", "") < fresh_cutoff

    todo = [r for r in universe.to_dict(orient="records") if needs_refresh(r)]
    if limit:
        todo = todo[:limit]
    log.info("fundamentals: %d of %d tickers need refresh", len(todo), len(universe))
    for n, r in enumerate(todo, 1):
        tk = r["ticker"]
        try:
            rec = fetch_fundamentals(tk, r["region"])
            rec["index"] = r["index"]
            rec["country"] = rec.get("country") or r["country"]
            store[tk] = rec
            log.info("[%d/%d] %s ok (%d years, %s)", n, len(todo), tk, len(rec["years"]), rec["source"])
        except Exception as e:
            log.warning("[%d/%d] %s failed: %s", n, len(todo), tk, e)
            time.sleep(2)
        if n % 25 == 0:
            save_json(FUND_FILE, store)  # checkpoint: a timed-out job resumes next run
    if not limit:  # full run: drop stocks that left every index
        keep = set(universe["ticker"])
        store = {k: v for k, v in store.items() if k in keep}
    save_json(FUND_FILE, store)


# ---------------------------------------------------------------- prices, news, results

def build_row(rec: dict, hist, min_growth: float) -> dict:
    years = [engine.Year(**y) for y in rec.get("years", [])]
    fx = rec.get("fx_fin_to_px") or 1.0
    div = rec.get("price_divisor") or 1.0
    b5 = engine.big5(years, min_growth) if years else {"metrics": {}, "score": 0.0, "all_pass": False, "years_of_data": 0}

    price = change = None
    tools = None
    if hist is not None and len(hist):
        h = hist / div
        price = float(h["Close"].iloc[-1])
        if len(h) > 1:
            change = price / float(h["Close"].iloc[-2]) - 1
        tools = tools_signal(h)
    price = price or rec.get("price")

    latest = years[-1] if years else None
    eps = rec.get("ttm_eps") if rec.get("ttm_eps") is not None else (latest.eps if latest else None)
    fcf = rec.get("ttm_fcf") if rec.get("ttm_fcf") is not None else (latest.fcf if latest else None)
    shares = rec.get("shares_outstanding")
    return {
        "ticker": rec["ticker"],
        "name": rec.get("name") or rec["ticker"],
        "sector": rec.get("sector") or "Unknown",
        "industry": rec.get("industry") or "Unknown",
        "region": rec.get("region"),
        "country": rec.get("country"),
        "index": rec.get("index"),
        "currency": rec.get("currency"),
        "source": rec.get("source"),
        "price": price,
        "change_1d": change,
        "market_cap": price * shares if price and shares else None,
        "eps_ttm": eps * fx if eps is not None else None,
        "eps_one_off": bool(rec.get("ttm_eps_one_off")),
        "fcf_ttm": fcf * fx if fcf is not None else None,
        "total_debt": latest.total_debt * fx if latest and latest.total_debt is not None else None,
        "hist_growth": engine.historical_growth(years),
        "analyst_growth": rec.get("analyst_growth"),
        "hist_pe": rec.get("hist_pe"),
        "big5": b5["metrics"],
        "big5_score": b5["score"],
        "big5_pass": b5["all_pass"],
        "years_of_data": b5["years_of_data"],
        "tools": tools,
        "fundamentals_updated": rec.get("updated"),
    }


def refresh_news(rows: list[dict], cfg: dict, store: dict) -> dict:
    ncfg = cfg["news"]
    lookback = ncfg["lookback_days"]
    min_score = ncfg.get("min_big5_score", 0.6)
    use_ai = ncfg.get("ai_summaries", False)
    ai_budget = int(ncfg.get("ai_max_calls_per_run", 20))
    ai_calls = 0
    for row in rows:
        if row["big5_score"] < min_score:
            continue
        tk = row["ticker"]
        items = news.google_news(row["name"], tk, lookback)
        if row["region"] == "US":
            try:
                items = sec.recent_8k_events(tk, lookback) + items
            except Exception as e:
                log.debug("8-K %s failed: %s", tk, e)
        prev = store.get(tk, {})
        flagged = [i for i in items if i["severity"] in ("high", "medium")]
        entry = {"items": items, "updated": now_iso(), "ai": prev.get("ai"), "ai_hash": prev.get("ai_hash")}
        digest = hashlib.sha1("|".join(sorted(i["title"] for i in flagged)).encode()).hexdigest()
        if use_ai and flagged and digest != prev.get("ai_hash") and ai_calls < ai_budget:
            ai_calls += 1  # counts attempts, including failed ones
            ai = news.summarize_flags(row["name"], tk, flagged, ncfg["ai_model"])
            if ai:
                entry["ai"], entry["ai_hash"] = ai, digest
        if not flagged:
            entry["ai"], entry["ai_hash"] = None, None
        store[tk] = entry
    if use_ai:
        log.info("AI summaries: %d of %d allowed calls used", ai_calls, ai_budget)
    return store


def attach_news(rows: list[dict], store: dict) -> None:
    for row in rows:
        entry = store.get(row["ticker"], {})
        items = entry.get("items", [])
        worst = max((news.SEVERITY_RANK[i["severity"]] for i in items), default=0)
        row["news_flag"] = {3: "high", 2: "medium", 1: "low"}.get(worst)
        row["news_flags"] = sum(1 for i in items if i["severity"] in ("high", "medium"))
        row["ai_risk"] = (entry.get("ai") or {}).get("risk")


def run_prices(cfg: dict, with_news: bool = True, send_alerts: bool = True) -> None:
    store = load_json(FUND_FILE, {})
    if not store:
        log.error("No fundamentals yet. Run: python -m rule1.scanner fundamentals")
        sys.exit(1)
    histories = market.daily_history(list(store))
    min_growth = cfg["rule1"]["min_growth"]
    rows = [build_row(rec, histories.get(tk), min_growth) for tk, rec in store.items()]

    news_store = load_json(NEWS_FILE, {})
    if with_news:
        news_store = refresh_news(rows, cfg, news_store)
        save_json(NEWS_FILE, news_store)
    attach_news(rows, news_store)

    # Keep chart data only for stocks worth looking at, to keep the file small.
    charts = {}
    for row in rows:
        h = histories.get(row["ticker"])
        if h is not None and row["big5_score"] >= 0.5:
            h = (h / (store[row["ticker"]].get("price_divisor") or 1.0)).tail(130)
            charts[row["ticker"]] = {
                "date": [d.strftime("%Y-%m-%d") for d in h.index],
                "high": h["High"].round(4).tolist(),
                "low": h["Low"].round(4).tolist(),
                "close": h["Close"].round(4).tolist(),
            }
    save_json(PRICES_FILE, charts)
    save_json(RESULTS_FILE, {"updated": now_iso(), "rows": rows})
    log.info("results: %d stocks, %d pass the Big 5", len(rows), sum(r["big5_pass"] for r in rows))
    if send_alerts and cfg["alerts"]["enabled"]:
        alerts.process(rows, news_store, cfg)


def run_one(ticker: str, cfg: dict) -> None:
    from .valuation import settings_from_config, value_row
    region = "US" if "." not in ticker else "Europe"
    rec = fetch_fundamentals(ticker, region)
    hist = market.daily_history([ticker]).get(ticker)
    row = build_row(rec, hist, cfg["rule1"]["min_growth"])
    row["valuation"] = value_row(row, settings_from_config(cfg))
    print(json.dumps(row, indent=2, default=str))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["fundamentals", "prices", "one"])
    p.add_argument("ticker", nargs="?")
    p.add_argument("--limit", type=int, help="only refresh this many tickers (testing)")
    p.add_argument("--indices", help="comma-separated index keys to scan instead of config (testing)")
    p.add_argument("--no-news", action="store_true")
    p.add_argument("--no-alerts", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    cfg = load_config()
    if a.indices:
        cfg["universe"]["indices"] = a.indices.split(",")
    if a.mode == "fundamentals":
        run_fundamentals(cfg, limit=a.limit)
    elif a.mode == "prices":
        run_prices(cfg, with_news=not a.no_news, send_alerts=not a.no_alerts)
    else:
        run_one(a.ticker, cfg)


if __name__ == "__main__":
    main()
