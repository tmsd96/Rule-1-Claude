"""Email alerts. Remembers what it already sent (data/alert_state.json), so you only
get an email when something new happens."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import smtplib
from email.mime.text import MIMEText

from .config import DATA_DIR
from .valuation import settings_from_config, value_row

log = logging.getLogger(__name__)
STATE_FILE = DATA_DIR / "alert_state.json"


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _money(v, ccy):
    return "n/a" if v is None else f"{v:,.2f} {ccy or ''}"


def find_events(rows: list[dict], news_store: dict, cfg: dict, state: dict) -> tuple[list[dict], dict]:
    """Return (new events, new state). Pure function apart from inputs: easy to test."""
    a = cfg["alerts"]
    s = settings_from_config(cfg)
    events, new_state = [], {}
    for row in rows:
        if a["sectors"] and row["sector"] not in a["sectors"]:
            continue
        if a["regions"] and row["region"] not in a["regions"]:
            continue
        if row["big5_score"] < a["min_big5_score"]:
            continue
        v = value_row(row, s)
        tk = row["ticker"]

        if a["on_sale"] and v["on_sale"]:
            key = f"onsale:{tk}"
            new_state[key] = state.get(key) or row.get("price")
            if key not in state:
                events.append({"type": "On sale", "row": row, "val": v,
                               "text": f"Price {_money(row['price'], row['currency'])} is at or below the "
                                       f"MOS price {_money(v['mos_price'], row['currency'])} "
                                       f"(sticker {_money(v['sticker'], row['currency'])})."})

        tools = row.get("tools") or {}
        if a["buy_signals"] and v["on_sale"] and tools.get("overall") == "buy":
            key = f"buy:{tk}"
            new_state[key] = True
            if key not in state:
                events.append({"type": "Buy signal", "row": row, "val": v,
                               "text": "On sale AND all three tools (MACD, Stochastics, 10-day MA) say buy."})

        if a["red_flags"]:
            entry = news_store.get(tk, {})
            for item in entry.get("items", []):
                if item["severity"] != "high":
                    continue
                key = "flag:" + tk + ":" + hashlib.sha1(item["title"].encode()).hexdigest()[:12]
                new_state[key] = item["published"]
                if key not in state:
                    ai = entry.get("ai") or {}
                    text = f"{item['title']} ({item['source']}, {item['published']})"
                    if ai.get("summary"):
                        text += f" — AI view ({ai.get('risk')} risk): {ai['summary']}"
                    events.append({"type": "Red flag", "row": row, "val": v, "text": text,
                                   "link": item["link"]})
    return events, new_state


def render_email(events: list[dict], dashboard_url: str | None) -> str:
    order = {"Red flag": 0, "Buy signal": 1, "On sale": 2}
    parts = ["<h2>Rule #1 screener alerts</h2>"]
    for e in sorted(events, key=lambda e: order[e["type"]]):
        r = e["row"]
        link = f' <a href="{html.escape(e["link"])}">source</a>' if e.get("link") else ""
        parts.append(
            f"<p><b>[{e['type']}] {html.escape(r['name'])} ({r['ticker']})</b> — {html.escape(r['sector'])}, "
            f"Big 5 score {r['big5_score']:.0%}<br>{html.escape(e['text'])}{link}</p>")
    if dashboard_url:
        parts.append(f'<p><a href="{html.escape(dashboard_url)}">Open the dashboard</a></p>')
    parts.append("<p style='color:#888'>Educational tool, not investment advice.</p>")
    return "\n".join(parts)


def send_email(subject: str, body_html: str) -> bool:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("ALERT_EMAIL_TO") or user
    if not (user and password and to):
        log.info("Email not configured (SMTP_USER/SMTP_PASSWORD); skipping send")
        return False
    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.sendmail(user, [x.strip() for x in to.split(",")], msg.as_string())
    return True


def process(rows: list[dict], news_store: dict, cfg: dict) -> list[dict]:
    state = _load_state()
    events, new_state = find_events(rows, news_store, cfg, state)
    if events:
        counts = {}
        for e in events:
            counts[e["type"]] = counts.get(e["type"], 0) + 1
        subject = "Rule #1: " + ", ".join(f"{n} {t.lower()}" for t, n in counts.items())
        try:
            sent = send_email(subject, render_email(events, os.environ.get("DASHBOARD_URL")))
        except Exception as e:
            log.error("Email failed: %s", e)
            return events  # keep old state so the alerts are retried next run
        if not sent:
            return events
        log.info("Sent %d alerts", len(events))
    STATE_FILE.write_text(json.dumps(new_state))
    return events
