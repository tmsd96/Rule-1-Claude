"""Turns a scanned stock row + user settings into sticker price, MOS price and checks.
Shared by the dashboard (live sliders) and the alert emails."""

from __future__ import annotations

from . import engine


def value_row(row: dict, s: dict) -> dict:
    """s keys: marr, margin_of_safety, growth_cap, pe_cap, projection_years,
    max_payback_years, max_debt_payoff_years, optional growth_override."""
    hist_g = row.get("hist_growth")
    analyst_g = row.get("analyst_growth")
    g = s.get("growth_override") or engine.choose_growth(hist_g, analyst_g, s["growth_cap"])
    st = engine.sticker_price(row.get("eps_ttm"), g, row.get("hist_pe"), s["marr"],
                              s.get("projection_years", 10), s.get("pe_cap", 50))
    sticker = st["sticker"] if st else None
    mos = engine.mos_price(sticker, s["margin_of_safety"])
    price = row.get("price")
    payback = engine.payback_years(row.get("market_cap"), row.get("fcf_ttm"), g)
    debt_years = engine.debt_payoff_years(row.get("total_debt"), row.get("fcf_ttm"))
    return {
        "growth_used": g,
        "sticker": sticker,
        "mos_price": mos,
        "future_eps": st["future_eps"] if st else None,
        "future_pe": st["future_pe"] if st else None,
        "future_price": st["future_price"] if st else None,
        "price_to_mos": price / mos if price and mos else None,
        "on_sale": bool(price and mos and price <= mos),
        "payback_years": payback,
        "payback_ok": payback is not None and payback <= s["max_payback_years"],
        "debt_years": debt_years,
        "debt_ok": debt_years is not None and debt_years <= s["max_debt_payoff_years"],
    }


def settings_from_config(cfg: dict) -> dict:
    r = cfg["rule1"]
    return {k: r[k] for k in ("marr", "margin_of_safety", "growth_cap", "pe_cap", "projection_years",
                              "max_payback_years", "max_debt_payoff_years", "min_growth")}
