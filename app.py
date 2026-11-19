"""Rule #1 screener dashboard. Run locally with:  streamlit run app.py"""

from __future__ import annotations

import json
import math
import os
from urllib.request import urlopen

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from rule1 import engine
from rule1.config import DATA_DIR, load_config
from rule1.technicals import macd, stochastic
from rule1.valuation import value_row

st.set_page_config(page_title="Rule #1 Screener", page_icon="📈", layout="wide")
CFG = load_config()


def _secret(name: str) -> str | None:
    try:
        return st.secrets.get(name) or os.environ.get(name)
    except Exception:
        return os.environ.get(name)


@st.cache_data(ttl=900, show_spinner="Loading scan results…")
def load(name: str, default):
    """Read data/<name>.json from DATA_BASE_URL (the GitHub 'data' branch) or the local folder."""
    base = _secret("DATA_BASE_URL")
    try:
        if base:
            with urlopen(f"{base.rstrip('/')}/{name}.json", timeout=30) as r:
                return json.loads(r.read())
        with open(DATA_DIR / f"{name}.json") as f:
            return json.load(f)
    except Exception:
        return default


results = load("results", {"rows": []})
rows = results.get("rows", [])
news_store = load("news", {})
charts = load("prices", {})

st.title("📈 Rule #1 Screener")
st.caption("Phil Town's 4 Ms (Meaning, Moat, Management, Margin of safety) applied to US, European and "
           "emerging-market stocks. "
           "Educational tool, not investment advice.")

if not rows:
    st.warning("No scan results yet. Run `python -m rule1.scanner fundamentals` and then "
               "`python -m rule1.scanner prices`, or wait for the scheduled GitHub Action.")
    st.stop()

# ---------------------------------------------------------------- sidebar
r1 = CFG["rule1"]
with st.sidebar:
    st.header("Filters")
    regions_all = [r for r in ("US", "Europe", "Emerging markets") if any(x["region"] == r for x in rows)]
    regions = st.multiselect("Market", regions_all, default=regions_all)
    countries_all = sorted({x.get("country") or "Other" for x in rows if x["region"] in regions})
    countries = st.multiselect("Country", countries_all, placeholder="All countries")
    sectors_all = sorted({r["sector"] for r in rows})
    sectors = st.multiselect("Sector", sectors_all, placeholder="All sectors")
    min_score = st.slider("Minimum Big 5 score", 0, 100, 80, 5, format="%d%%",
                          help="Share of the Big 5 checks (each growth rate and ROIC over 10/5/3/1 years) "
                               "that are at least 10%.") / 100
    only_sale = st.toggle("Only stocks on sale (price ≤ MOS price)")
    hide_flags = st.toggle("Hide stocks with red-flag news")
    st.header("Rule #1 settings")
    mos = st.slider("Margin of safety", 0, 80, int(r1["margin_of_safety"] * 100), 5, format="%d%%",
                    help="Buy price = sticker price × (1 − margin of safety). Phil Town uses 50%.") / 100
    marr = st.slider("Minimum return (MARR)", 5, 25, int(r1["marr"] * 100), 1, format="%d%%") / 100
    cap = st.slider("Growth cap", 5, 30, int(r1["growth_cap"] * 100), 1, format="%d%%",
                    help="Maximum yearly growth used in the 10-year projection.") / 100
    st.caption(f"Data updated {results.get('updated', '?').replace('T', ' ').replace('Z', ' UTC')}")

settings = {**r1, "margin_of_safety": mos, "marr": marr, "growth_cap": cap}

# ---------------------------------------------------------------- table
records = []
for r in rows:
    v = value_row(r, settings)
    records.append({**r, **v})
df = pd.DataFrame(records)
view = df[df["region"].isin(regions) & (df["big5_score"] >= min_score)]
if sectors:
    view = view[view["sector"].isin(sectors)]
if countries:
    view = view[view["country"].fillna("Other").isin(countries)]
if only_sale:
    view = view[view["on_sale"]]
if hide_flags:
    view = view[view["news_flag"] != "high"]

TOOL_ICON = {"buy": "🟢", "sell": "🔴", "mixed": "🟡"}
FLAG_ICON = {"high": "⚠️ high", "medium": "• medium", "low": "", None: ""}


def tools_text(t):
    if not isinstance(t, dict):
        return ""
    return " ".join(TOOL_ICON[t[k]] for k in ("macd", "stochastic", "ma10"))


def debt_text(d):
    if d is None or (isinstance(d, float) and math.isnan(d)):
        return None
    return 99.0 if math.isinf(d) else d


view = view.assign(
    tools_txt=view["tools"].map(tools_text),
    news_txt=view["news_flag"].map(lambda f: FLAG_ICON.get(f, "")),
    debt_val=view["debt_years"].map(debt_text),
    above_mos=(view["price"] / view["mos_price"] - 1),
    score_pct=view["big5_score"] * 100,
).sort_values(["on_sale", "price_to_mos"], ascending=[False, True], na_position="last")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Stocks scanned", len(df))
c2.metric("Match your filters", len(view))
c3.metric("On sale", int(view["on_sale"].sum()))
c4.metric("With red-flag news", int((view["news_flag"] == "high").sum()))

# ---------------------------------------------------------------- analysis (shared)
def render_analysis(tk: str, where: str) -> None:
    """Full Rule #1 analysis of one stock. `where` keeps widget keys unique, because the
    same analysis can be shown in the Screener tab and in the Stock analysis tab."""
    row = next(r for r in rows if r["ticker"] == tk)
    g_auto = engine.choose_growth(row.get("hist_growth"), row.get("analyst_growth"), cap)
    g_pick = st.slider("Growth rate for the projection", 0.0, 30.0, float(round((g_auto or 0) * 100, 1)), 0.5,
                       format="%.1f%%", key=f"growth_{where}_{tk}",
                       help="Default = lower of historical equity growth and analyst estimate, capped.") / 100
    v = value_row(row, {**settings, "growth_override": g_pick or None})
    ccy = row.get("currency") or ""

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Price", f"{row['price']:,.2f} {ccy}" if row.get("price") else "n/a",
              f"{row['change_1d']:+.1%}" if row.get("change_1d") is not None else None)
    m2.metric("Sticker price", f"{v['sticker']:,.2f}" if v["sticker"] else "n/a")
    m3.metric(f"MOS price ({mos:.0%})", f"{v['mos_price']:,.2f}" if v["mos_price"] else "n/a")
    m4.metric("Big 5 score", f"{row['big5_score']:.0%}", f"{row['years_of_data']} years of data",
              delta_color="off")
    m5.metric("Payback time", f"{v['payback_years']} y" if v["payback_years"] else "n/a",
              "OK" if v["payback_ok"] else "over 8 y", delta_color="normal" if v["payback_ok"] else "inverse")

    if v["on_sale"]:
        st.success("On sale: the price is at or below the margin-of-safety price.")
    elif v["mos_price"]:
        st.info(f"Not on sale. The price would need to fall {1 - v['mos_price'] / row['price']:.0%} "
                f"to reach the MOS price.")

    left, right = st.columns([1, 1])
    with left:
        st.subheader("Moat: the Big 5")
        tbl = []
        for key in engine.BIG5:
            m = row["big5"].get(key, {})
            w = m.get("windows", {})
            line = {"Metric": engine.BIG5_LABELS[key]}
            for k in sorted(w, key=int, reverse=True):
                line[f"{k} y"] = float("nan") if w[k] is None else w[k]
            line["Pass"] = {True: "✅", False: "❌", None: "–"}[m.get("passed")]
            tbl.append(line)
        tdf = pd.DataFrame(tbl).set_index("Metric")
        num_cols = [c for c in tdf.columns if c != "Pass"]
        nums = tdf[num_cols].astype(float)
        # Show text (so missing values read "n/a", not "None") and colour from the numbers.
        shown = tdf.copy()
        shown[num_cols] = nums.map(lambda x: "n/a" if math.isnan(x) else f"{x:.1%}")
        colours = nums.map(lambda x: "" if math.isnan(x) else
                           ("color:#1a7f37" if x >= 0.10 else "color:#cf222e"))
        st.dataframe(shown.style.apply(lambda _: colours, axis=None, subset=num_cols),
                     use_container_width=True, key=f"big5_{where}")
        st.caption(f"Source: {row.get('source') or 'n/a'}. Each number should be ≥ 10%.")

        st.subheader("Margin of safety: sticker price")
        if v["sticker"]:
            st.markdown(f"""
| Step | Value |
|---|---|
| Current EPS (TTM) | {row['eps_ttm']:,.2f} {ccy}{' (one-off quarter removed)' if row.get('eps_one_off') else ''} |
| Growth rate used | {v['growth_used']:.1%} (historical {row['hist_growth'] or 0:.1%}, analysts {'n/a' if row.get('analyst_growth') is None else f"{row['analyst_growth']:.1%}"}) |
| EPS in 10 years | {v['future_eps']:,.2f} |
| Future P/E (lower of 2 × growth and historical {'n/a' if not row.get('hist_pe') else f"{row['hist_pe']:.0f}"}) | {v['future_pe']:.1f} |
| Price in 10 years | {v['future_price']:,.2f} |
| **Sticker price** (discounted at {marr:.0%}/yr) | **{v['sticker']:,.2f}** |
| **MOS price** ({mos:.0%} off) | **{v['mos_price']:,.2f}** |
""")
        else:
            st.write("No sticker price: earnings or growth are negative or missing.")

    with right:
        st.subheader("Management")
        dy = v["debt_years"]
        st.markdown(f"- Debt payable from free cash flow in **{'n/a' if dy is None else ('never (negative FCF)' if math.isinf(dy) else f'{dy:.1f} years')}** "
                    f"{'✅' if v['debt_ok'] else '❌'} (target ≤ 3)")
        roic = row["big5"].get("roic", {}).get("windows", {})
        if roic:
            st.markdown(f"- ROIC, latest year: **{roic.get('1', 0) or 0:.0%}** — management's return on the money it invests")

        entry = news_store.get(tk, {})
        ai = entry.get("ai")
        if ai:
            st.subheader(f"AI news read: {ai['risk']} risk")
            st.write(ai["summary"])
            st.caption(f"Management: {ai['management']}  \nMoat: {ai['moat']}")
        st.subheader("Recent news")
        items = entry.get("items", [])
        if not items:
            st.caption("No news fetched (news is checked for stocks with a Big 5 score of 60%+).")
        for it in sorted(items, key=lambda i: (-{"high": 3, "medium": 2, "low": 1}.get(i["severity"], 0),
                                               i["published"]), reverse=False)[:15]:
            badge = {"high": "⚠️ ", "medium": "• ", "low": ""}.get(it["severity"], "")
            area = f" _({it['category']})_" if it.get("category") else ""
            st.markdown(f"{badge}[{it['title']}]({it['link']}) — {it.get('source', '')}, {it['published']}{area}")

    st.subheader("Timing: Phil Town's three tools")
    ch = charts.get(tk)
    if ch:
        h = pd.DataFrame({"High": ch["high"], "Low": ch["low"], "Close": ch["close"]},
                         index=pd.to_datetime(ch["date"]))
        mc, so = macd(h["Close"]), stochastic(h["High"], h["Low"], h["Close"])
        fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.5, 0.25, 0.25],
                            vertical_spacing=0.04, subplot_titles=("Price, 10-day MA, MOS & sticker price", "MACD (8,17,9)",
                                                                   "Stochastics (14,5)"))
        fig.add_trace(go.Scatter(x=h.index, y=h["Close"], name="Price", line=dict(color="#2f6fde")), 1, 1)
        fig.add_trace(go.Scatter(x=h.index, y=h["Close"].rolling(10).mean(), name="10-day MA",
                                 line=dict(color="#e39b17")), 1, 1)
        if v["mos_price"]:
            fig.add_hline(y=v["mos_price"], line_dash="dot", line_color="#1a7f37",
                          annotation_text=f"MOS price {v['mos_price']:,.2f}",
                          annotation_position="top left", row=1, col=1)
        # Price range plus the MOS line (always shown).
        lo = min([h["Low"].min(), *([v["mos_price"]] if v["mos_price"] else [])])
        hi = max([h["High"].max(), *([v["mos_price"]] if v["mos_price"] else [])])
        sticker = v["sticker"]
        # The sticker line is only drawn when it fits without squashing the price chart:
        # it may at most double the visible range. Otherwise its value is shown as a note.
        if sticker and (hi - lo) and max(hi, sticker) - min(lo, sticker) <= 2 * (hi - lo):
            fig.add_hline(y=sticker, line_dash="dash", line_color="#8250df",
                          annotation_text=f"Sticker price {sticker:,.2f}",
                          annotation_position="bottom left", row=1, col=1)
            lo, hi = min(lo, sticker), max(hi, sticker)
        elif sticker:
            where_txt = "above" if sticker > hi else "below"
            fig.add_annotation(text=f"Sticker price {sticker:,.2f} ({where_txt} the chart)",
                               xref="x domain", yref="y domain", x=0.99, y=0.98 if sticker > hi else 0.02,
                               xanchor="right", yanchor="top" if sticker > hi else "bottom",
                               showarrow=False, font=dict(color="#8250df"), row=1, col=1)
        pad = (hi - lo) * 0.10
        fig.update_yaxes(range=[lo - pad, hi + pad], row=1, col=1)
        fig.add_trace(go.Bar(x=h.index, y=mc["hist"], name="MACD hist",
                             marker_color=["#1a7f37" if x > 0 else "#cf222e" for x in mc["hist"]]), 2, 1)
        fig.add_trace(go.Scatter(x=h.index, y=mc["macd"], name="MACD", line=dict(color="#2f6fde")), 2, 1)
        fig.add_trace(go.Scatter(x=h.index, y=mc["signal"], name="Signal", line=dict(color="#e39b17")), 2, 1)
        fig.add_trace(go.Scatter(x=h.index, y=so["k"], name="%K", line=dict(color="#2f6fde")), 3, 1)
        fig.add_trace(go.Scatter(x=h.index, y=so["d"], name="%D", line=dict(color="#e39b17")), 3, 1)
        fig.update_layout(height=620, showlegend=False, margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, use_container_width=True, key=f"chart_{where}")
        t = row.get("tools") or {}
        st.caption(f"MACD {TOOL_ICON.get(t.get('macd'), '')} · Stochastics {TOOL_ICON.get(t.get('stochastic'), '')} · "
                   f"10-day MA {TOOL_ICON.get(t.get('ma10'), '')}. Phil Town buys when all three are green "
                   "and the price is below the MOS price.")
    else:
        st.caption("No chart data for this stock.")


tab_list, tab_detail, tab_help = st.tabs(["Screener", "Stock analysis", "How it works"])

with tab_list:
    table = view.reset_index(drop=True)
    event = st.dataframe(
        table[["ticker", "name", "sector", "region", "currency", "price", "mos_price", "sticker", "above_mos",
              "score_pct", "years_of_data", "payback_years", "debt_val", "tools_txt", "news_txt"]],
        hide_index=True, use_container_width=True, height=520,
        on_select="rerun", selection_mode="single-row", key="screener_table",
        column_config={
            "ticker": "Ticker", "name": "Company", "sector": "Sector", "region": "Market",
            "currency": "Ccy",
            "price": st.column_config.NumberColumn("Price", format="%.2f"),
            "mos_price": st.column_config.NumberColumn("MOS price", format="%.2f",
                                                       help="Rule #1 buy price"),
            "sticker": st.column_config.NumberColumn("Sticker", format="%.2f"),
            "above_mos": st.column_config.NumberColumn("Above MOS", format="percent",
                                                       help="How far the price is above the MOS price. "
                                                            "Zero or negative = on sale."),
            "score_pct": st.column_config.ProgressColumn("Big 5", format="%.0f%%", min_value=0, max_value=100),
            "years_of_data": st.column_config.NumberColumn("Years", help="Years of financial history used"),
            "payback_years": st.column_config.NumberColumn("Payback (y)", help="Target ≤ 8"),
            "debt_val": st.column_config.NumberColumn("Debt (y FCF)", format="%.1f", help="Target ≤ 3"),
            "tools_txt": st.column_config.TextColumn("Tools", help="MACD · Stochastics · 10-day MA"),
            "news_txt": st.column_config.TextColumn("News"),
        },
    )
    st.caption("Tools: 🟢 buy · 🔴 sell (MACD 8/17/9, Stochastics 14/5, 10-day moving average). "
               "Non-US stocks have ~4 years of history (free data limit); US stocks use 10+ years from SEC filings. "
               "Prices are in each stock's own currency.")

    selected = event.selection.rows if event else []
    if selected:
        tk_sel = table.iloc[selected[0]]["ticker"]
        st.session_state["picked"] = tk_sel  # also pre-selects it in the Stock analysis tab
        st.divider()
        st.header(f"{tk_sel} — {table.iloc[selected[0]]['name']}")
        render_analysis(tk_sel, "screener")
    else:
        st.info("Tick the box at the left of a row to analyse that stock here.")

# ---------------------------------------------------------------- detail tab
with tab_detail:
    options = view["ticker"].tolist() or df["ticker"].tolist()
    labels = dict(zip(df["ticker"], df["name"]))
    picked = st.session_state.get("picked")
    start_at = options.index(picked) if picked in options else 0
    tk_detail = st.selectbox("Stock", options, index=start_at,
                             format_func=lambda t: f"{t} — {labels.get(t, '')}")
    render_analysis(tk_detail, "tab")

with tab_help:
    st.markdown("""
### The method
1. **Meaning** – only buy businesses you understand. This one is yours to judge.
2. **Moat** – the *Big 5 numbers* must each be ≥ 10% a year over 10, 5, 3 and 1 years:
   ROIC, sales growth, EPS growth, equity (book value per share) growth, operating cash flow growth.
3. **Management** – debt payable with ≤ 3 years of free cash flow, high ROIC, no red-flag news
   (executive departures, fraud, investigations, restatements…).
4. **Margin of safety** – *Sticker price* = EPS × (1+g)¹⁰ × future P/E, discounted at 15%/yr.
   Buy at a discount (usually 50%) to the sticker price. *Payback time* (≤ 8 years) is a second check.

### Data sources (all free)
- **SEC EDGAR** – 10+ years of audited US financials and 8-K event filings.
- **Yahoo Finance** – prices, European financials (≈ 4 years), analyst growth estimates, currencies.
- **Wikipedia** – index members: US (S&P 500, 400, 600), Europe (EURO STOXX 50, FTSE 100 and 250, DAX,
  CAC 40, AEX, IBEX 35, PSI, SMI, OMX 30, FTSE MIB) and emerging markets (Hang Seng, CSI 300, KOSPI 200,
  NIFTY 50, B3 Brazil). Taiwan uses a fixed list of its ~60 largest companies.
- **Google News** – headlines, tagged for red flags; optionally summarised by Claude.

### Updates
Everything runs once a day at 17:15 UTC: prices, tools and news every day, financials for each stock
once a week. Email alerts go out
when a strong-moat stock goes on sale, gets an all-green buy signal, or has new high-severity news.
""")
