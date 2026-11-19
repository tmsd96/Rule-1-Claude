"""Recent news with red-flag detection.

Sources (all free): Google News RSS (any company, any market) and SEC 8-K filings (US).
Headlines are tagged by keyword rules; optionally Claude writes a short Rule #1 risk summary."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import feedparser
import requests

log = logging.getLogger(__name__)

# (regex, severity, Rule #1 area). Checked in order; the first match sets the tag.
RULES = [
    (r"\b(resign|resigns|resigned|steps? down|stepping down|ousted|fired|departure of|exits? as)\b.*\b(ceo|cfo|chief|chair|founder|president)\b|\b(ceo|cfo|chief executive|chair|founder)\b.*\b(resign|steps? down|ousted|fired|leaves|exit)", "high", "management"),
    (r"\b(fraud|embezzl|accounting irregular|restat(e|es|ement)|misstat|whistleblower)", "high", "management"),
    (r"\b(sec|doj|ftc|fca|bafin|amf) (probe|investigation|charges?|subpoena)|\b(subpoena|indicted|indictment|criminal (probe|investigation)|raided)", "high", "management"),
    (r"\b(bankrupt|chapter 11|insolven|default(s|ed)? on|going concern|administration proceedings)", "high", "margin"),
    (r"\b(profit warning|cuts? (its |full-year |annual )?(guidance|forecast|outlook)|lowers? (guidance|forecast|outlook)|slashes? (guidance|forecast|outlook)|withdraws? guidance)", "high", "moat"),
    (r"\b(short[- ]seller|short report)", "high", "management"),
    (r"\b(delist|trading halted|suspended from trading)", "high", "margin"),
    (r"\b(investigation|probe|antitrust|anti-trust|sanction|export (ban|curbs?|restrictions?)|fined|penalty)", "medium", "moat"),
    (r"\b(class action|lawsuit|sued|sues|litigation)", "medium", "management"),
    (r"\b(recall|data breach|cyberattack|hack(ed)?|outage)", "medium", "moat"),
    (r"\b(downgrade[sd]?|cut to (sell|underperform|neutral)|price target (cut|lowered))", "medium", "margin"),
    (r"\b(miss(es|ed)? (estimates|expectations|forecasts)|disappointing results|revenue (fall|drop|decline)s?)", "medium", "moat"),
    (r"\b(layoffs?|job cuts|restructuring|strike)", "low", "moat"),
]
_COMPILED = [(re.compile(p, re.I), s, a) for p, s, a in RULES]
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1, None: 0}


def classify(title: str) -> tuple[str | None, str | None]:
    for rx, severity, area in _COMPILED:
        if rx.search(title):
            return severity, area
    return None, None


_SUFFIXES = re.compile(r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|plc|ltd|limited|se|sa|s\.a|ag|nv|n\.v|ab|spa|s\.p\.a|holdings?|group|class [a-z])\.?$", re.I)


def search_name(name: str) -> str:
    n = name.strip()
    for _ in range(3):
        n = _SUFFIXES.sub("", n).strip()
    return n


def google_news(name: str, ticker: str, lookback_days: int = 14, limit: int = 25) -> list[dict]:
    query = f'"{search_name(name)}" when:{lookback_days}d'
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        for attempt in range(2):
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 rule1-screener"})
            if r.status_code != 429 or attempt:
                break
            time.sleep(20)  # rate limited: wait once, then retry
        r.raise_for_status()
    except Exception as e:
        log.warning("news %s failed: %s", ticker, e)
        return []
    feed = feedparser.parse(r.content)
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    items = []
    for e in feed.entries[:limit]:
        try:
            published = parsedate_to_datetime(e.published)
        except Exception:
            continue
        if published < cutoff:
            continue
        title = re.sub(r"\s+-\s+[^-]+$", "", e.title)  # strip " - Source"
        severity, area = classify(title)
        items.append({
            "title": title,
            "link": e.link,
            "published": published.date().isoformat(),
            "source": getattr(e, "source", {}).get("title", "") if hasattr(e, "source") else "",
            "severity": severity,
            "category": area,
        })
    time.sleep(1.0)  # be polite to Google News
    return items


def summarize_flags(name: str, ticker: str, items: list[dict], model: str) -> dict | None:
    """Ask Claude for a short Rule #1 risk read of the flagged headlines.

    Returns {"risk": "none|low|medium|high", "summary": str, "four_ms": {...}} or None.
    Needs ANTHROPIC_API_KEY; any failure returns None and the keyword flags still apply."""
    if not os.environ.get("ANTHROPIC_API_KEY") or not items:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    headlines = "\n".join(f"- [{i['published']}] {i['title']} ({i.get('source') or 'news'})" for i in items[:20])
    schema = {
        "type": "object",
        "properties": {
            "risk": {"type": "string", "enum": ["none", "low", "medium", "high"]},
            "summary": {"type": "string"},
            "management": {"type": "string"},
            "moat": {"type": "string"},
        },
        "required": ["risk", "summary", "management", "moat"],
        "additionalProperties": False,
    }
    prompt = (
        f"You help a long-term value investor who follows Phil Town's Rule #1 (the 4 Ms: Meaning, "
        f"Moat, Management, Margin of safety). Below are recent headlines about {name} ({ticker}).\n\n"
        f"{headlines}\n\n"
        "Judge only from these headlines. Ignore routine noise such as daily price moves, analyst "
        "chatter and promotional articles. Return: risk = how much these events threaten the "
        "long-term thesis (none/low/medium/high); summary = 2-3 plain sentences on what happened and "
        "why it matters; management and moat = one sentence each on the impact on that M, or "
        "'No clear impact'. Say when a headline is unconfirmed or from a weak source."
    )
    client = anthropic.Anthropic()
    # Server-side refusal fallback is supported on Opus/Sonnet/Fable, not on Haiku.
    extra = {} if "haiku" in model else {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=4000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
            **extra,
        )
    except anthropic.RateLimitError:
        log.warning("Claude rate limited for %s; skipping summary", ticker)
        return None
    except anthropic.APIStatusError as e:
        log.warning("Claude API error for %s: %s %s", ticker, e.status_code, e.message)
        return None
    except anthropic.APIConnectionError:
        log.warning("Claude connection error for %s", ticker)
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        return None
    text = next((b.text for b in response.content if b.type == "text"), None)
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None
