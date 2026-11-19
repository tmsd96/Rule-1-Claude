import math

import numpy as np
import pandas as pd
import pytest

from rule1 import alerts, engine, market, news, sec
from rule1.technicals import tools_signal
from rule1.universe import _clean_ticker
from rule1.valuation import value_row

CFG = {
    "rule1": {"min_growth": 0.10, "marr": 0.15, "margin_of_safety": 0.50, "growth_cap": 0.15,
              "pe_cap": 50, "max_payback_years": 8, "max_debt_payoff_years": 3, "projection_years": 10},
    "alerts": {"enabled": True, "sectors": [], "regions": ["US", "Europe"], "min_big5_score": 0.8,
               "on_sale": True, "red_flags": True, "buy_signals": True},
}


def growing_years(n=11, g=0.2, start=2015):
    return [engine.Year(year=start + i, revenue=100 * (1 + g) ** i, eps=1 * (1 + g) ** i,
                        net_income=20 * (1 + g) ** i, ocf=30 * (1 + g) ** i, capex=5,
                        equity=80 * (1 + g) ** i, shares=10, total_debt=10) for i in range(n)]


# ---------------------------------------------------------------- engine

def test_cagr():
    assert engine.cagr(100, 200, 1) == pytest.approx(1.0)
    assert engine.cagr(100, 259.374, 10) == pytest.approx(0.10, abs=1e-5)
    assert engine.cagr(-5, 10, 3) is None
    assert engine.cagr(10, -5, 3) is None


def test_big5_all_pass_for_steady_grower():
    r = engine.big5(growing_years())
    assert r["all_pass"] and r["score"] == 1.0 and r["years_of_data"] == 11
    assert r["metrics"]["sales"]["windows"]["10"] == pytest.approx(0.2)


def test_big5_fails_when_eps_turns_negative():
    ys = growing_years()
    ys[-1].eps = -0.5
    r = engine.big5(ys)
    assert r["metrics"]["eps"]["passed"] is False
    assert not r["all_pass"]


def test_big5_short_history_uses_longest_window():
    r = engine.big5(growing_years(n=4))
    assert set(r["metrics"]["sales"]["windows"]) == {"3", "1"}
    assert set(r["metrics"]["roic"]["windows"]) == {"4", "3", "1"}


def test_sticker_matches_hand_calculation_for_nvda():
    # The worked NVDA example: EPS 7.91, 15% growth, future P/E 30, MARR 15%.
    st = engine.sticker_price(7.91, 0.15, hist_pe=52.6, marr=0.15)
    assert st["future_pe"] == pytest.approx(30)
    assert st["future_eps"] == pytest.approx(32.00, abs=0.01)
    assert st["sticker"] == pytest.approx(237.3, abs=0.1)
    assert engine.mos_price(st["sticker"], 0.5) == pytest.approx(118.65, abs=0.1)


def test_sticker_uses_lower_historical_pe_and_rejects_losses():
    assert engine.sticker_price(2, 0.15, hist_pe=12)["future_pe"] == 12
    assert engine.sticker_price(-1, 0.15, None) is None


def test_choose_growth_takes_lower_and_caps():
    assert engine.choose_growth(0.40, 0.12, cap=0.15) == 0.12
    assert engine.choose_growth(0.40, None, cap=0.15) == 0.15
    assert engine.choose_growth(-0.05, None, cap=0.15) is None


def test_payback_and_debt():
    # Same as the hand-worked NVDA case: 3.75T cap, 127B FCF, 15% growth -> 12 years
    assert engine.payback_years(3.75e12, 127e9, 0.15) == 12
    assert engine.payback_years(1000, -5, 0.1) is None
    assert engine.debt_payoff_years(0, 10) == 0
    assert engine.debt_payoff_years(30, 10) == 3
    assert math.isinf(engine.debt_payoff_years(30, -1))


# ---------------------------------------------------------------- SEC parsing

def test_sec_annual_facts_prefers_latest_filing_and_skips_quarters():
    gaap = {"Revenues": {"units": {"USD": [
        {"start": "2020-01-01", "end": "2020-12-31", "val": 100, "form": "10-K", "filed": "2021-02-01"},
        {"start": "2020-01-01", "end": "2020-12-31", "val": 105, "form": "10-K", "filed": "2022-02-01"},
        {"start": "2020-10-01", "end": "2020-12-31", "val": 30, "form": "10-K", "filed": "2021-02-01"},
        {"start": "2021-01-01", "end": "2021-12-31", "val": 120, "form": "10-Q", "filed": "2022-02-01"},
    ]}}}
    out = sec._annual_facts(gaap, ["Revenues"], duration=True)
    assert out == {"2020-12-31": (105.0, "2022-02-01")}


def test_split_factor_only_counts_later_splits():
    splits = [("2021-07-20", 4.0), ("2024-06-10", 10.0)]
    assert sec._split_factor("2020-02-01", splits) == 40
    assert sec._split_factor("2022-03-01", splits) == 10
    assert sec._split_factor("2025-03-01", splits) == 1


# ---------------------------------------------------------------- technicals

def _ohlc(prices):
    s = pd.Series(prices, index=pd.date_range("2026-01-01", periods=len(prices)))
    return pd.DataFrame({"High": s * 1.01, "Low": s * 0.99, "Close": s})


def test_tools_buy_in_uptrend_sell_in_downtrend():
    up = np.concatenate([np.linspace(100, 80, 50), np.linspace(80, 92, 8)])  # bounce off a low
    down = np.concatenate([np.linspace(80, 110, 40), np.linspace(110, 85, 20)])
    assert tools_signal(_ohlc(up))["overall"] == "buy"
    assert tools_signal(_ohlc(down))["overall"] == "sell"
    assert tools_signal(_ohlc(up[:10])) is None


# ---------------------------------------------------------------- news flags

@pytest.mark.parametrize("title,severity,area", [
    ("Jensen Huang resigns as Nvidia CEO, and Colette Kress takes over", "high", "management"),
    ("Nvidia CEO steps down after 33 years", "high", "management"),
    ("Company X issues profit warning as demand slows", "high", "moat"),
    ("Retailer files for Chapter 11 bankruptcy", "high", "margin"),
    ("EU opens antitrust investigation into chipmaker", "medium", "moat"),
    ("Morgan Stanley downgrades Apple", "medium", "margin"),
    ("Nvidia unveils new GPU at conference", None, None),
])
def test_classify(title, severity, area):
    assert news.classify(title) == (severity, area)


def test_search_name_strips_legal_suffixes():
    assert news.search_name("NVIDIA Corporation") == "NVIDIA"
    assert news.search_name("SAP SE") == "SAP"
    assert news.search_name("Apple Inc.") == "Apple"


# ---------------------------------------------------------------- universe

def test_clean_ticker():
    assert _clean_ticker("BRK.B", "", "US") == "BRK-B"
    assert _clean_ticker("BT.A", ".L", "Europe") == "BT-A.L"
    assert _clean_ticker("EDP", ".LS", "Europe") == "EDP.LS"
    assert _clean_ticker("ASML.AS", "", "Europe") == "ASML.AS"


# ---------------------------------------------------------------- valuation + alerts

def _row(**kw):
    row = {"ticker": "ABC", "name": "ABC Corp", "sector": "Technology", "region": "US", "currency": "USD",
           "price": 50.0, "eps_ttm": 5.0, "hist_growth": 0.2, "analyst_growth": None, "hist_pe": 40,
           "market_cap": 5e9, "fcf_ttm": 6e8, "total_debt": 1e8, "big5_score": 1.0,
           "tools": {"overall": "buy"}}
    row.update(kw)
    return row


def test_value_row_on_sale():
    v = value_row(_row(), {**CFG["rule1"]})
    # EPS 5, g 15% -> future EPS 20.23, PE 30 -> 606.8, /4.0456 = 150 sticker, MOS 75
    assert v["sticker"] == pytest.approx(150.0, abs=0.1)
    assert v["on_sale"] and v["debt_ok"]


def test_alerts_only_fire_once():
    news_store = {"ABC": {"items": [{"title": "ABC CEO resigns", "severity": "high", "source": "X",
                                     "published": "2026-11-18", "link": "http://x"}]}}
    events, state = alerts.find_events([_row()], news_store, CFG, {})
    assert {e["type"] for e in events} == {"On sale", "Buy signal", "Red flag"}
    events2, _ = alerts.find_events([_row()], news_store, CFG, state)
    assert events2 == []
    # weak moat -> no alerts at all
    events3, _ = alerts.find_events([_row(big5_score=0.5)], news_store, CFG, {})
    assert events3 == []


def test_smooth_ttm_removes_one_off_quarter_only():
    # Alphabet 2026: one quarter inflated by an investment gain
    eps, adj = market.smooth_ttm([2.12, 9.11, 2.30, 2.82])
    assert adj and eps == pytest.approx(2.12 + 2.30 + 2.30 + 2.82)
    # NVIDIA: fast but steady growth is left alone
    eps, adj = market.smooth_ttm([2.70, 2.00, 1.80, 1.30])
    assert not adj and eps == pytest.approx(7.80)
