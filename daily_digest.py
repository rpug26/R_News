"""Hive RNS Daily Digest – Option A format, posted once per weekday."""
from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timezone
from html import escape

import requests

DIGEST_ENABLED = os.getenv("DIGEST_ENABLED", "1").strip() not in ("0", "false", "False", "no")
DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "16"))
DIGEST_MINUTE = int(os.getenv("DIGEST_MINUTE", "45"))
DIGEST_STATE_FILE = os.getenv(
    "DIGEST_STATE_FILE",
    os.path.join(os.getenv("STATE_DIR", "."), "last_digest_date.txt"),
)

_PRICE_KEYWORDS = (
    "placing", "fundraise", "fundraising", "subscription", "retail offer",
    "trading update", "interim", "final results", "audited results",
    "profit", "revenue", "contract", "offtake", "drill", "acquisition",
    "disposal", "suspension", "restoration", "capital access", "gm ",
    "general meeting", "circular", "ceo share", "director dealing",
    "cash generation", "production", "dividend",
)
_HOLDING_KEYWORDS = (
    "holding(s) in company", "holdings in company", "total voting rights",
    "exercise of", "admission of", "director/pdmr", "pdmr",
)


def _short_ai(text, limit=140):
    t = (text or "").strip()
    if not t or "AI Summary Disclaimer" in t:
        return ""
    t = t.replace("Disclaimer*", "").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 1].rsplit(" ", 1)[0] + "…"


def _bucket(title: str) -> str:
    low = (title or "").lower()
    if any(k in low for k in _HOLDING_KEYWORDS) and not any(
        k in low for k in ("results", "fundraise", "placing", "trading update")
    ):
        return "holding"
    if any(k in low for k in _PRICE_KEYWORDS):
        return "price"
    return "other"


def _plain_from_prop(prop):
    if not prop or not isinstance(prop, dict):
        return ""
    t = prop.get("type")
    if t == "title":
        return "".join(x.get("plain_text", "") for x in prop.get("title", [])).strip()
    if t == "rich_text":
        return "".join(x.get("plain_text", "") for x in prop.get("rich_text", [])).strip()
    if t == "url":
        return (prop.get("url") or "").strip()
    return ""


def _normalize_uuid(raw):
    raw = (raw or "").replace("-", "").strip()
    if len(raw) == 32:
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    return raw


def fetch_rns_for_date(date_iso: str, notion_token: str, rns_db_id: str, headers: dict):
    if not notion_token or not rns_db_id:
        return []
    db_id = _normalize_uuid(rns_db_id)
    items = []
    has_more = True
    start_cursor = None
    while has_more:
        body = {
            "page_size": 100,
            "filter": {"property": "RNS Date", "date": {"equals": date_iso}},
        }
        if start_cursor:
            body["start_cursor"] = start_cursor
        try:
            res = requests.post(
                f"https://api.notion.com/v1/databases/{db_id}/query",
                headers=headers,
                json=body,
                timeout=30,
            )
            if res.status_code != 200:
                print(f"digest query failed {res.status_code}: {res.text[:300]}")
                break
            data = res.json()
            for page in data.get("results", []):
                props = page.get("properties", {})
                ticker = _plain_from_prop(props.get("Ticker")).upper().lstrip("#")
                title = _plain_from_prop(props.get("Title"))
                company = _plain_from_prop(props.get("Company"))
                link = _plain_from_prop(props.get("Link"))
                summary = _plain_from_prop(props.get("AI Summary"))
                if not ticker and not title:
                    continue
                items.append(
                    {
                        "ticker": ticker or "—",
                        "title": title or "(no title)",
                        "company": company,
                        "link": link,
                        "summary": summary,
                        "bucket": _bucket(title),
                    }
                )
            has_more = bool(data.get("has_more"))
            start_cursor = data.get("next_cursor")
        except Exception as e:
            print(f"fetch_rns_for_date error: {e}")
            break
    return items


def format_daily_digest(date_iso: str, items: list) -> str:
    day_label = date_iso
    try:
        day_label = datetime.strptime(date_iso, "%Y-%m-%d").strftime("%A %d %b %Y")
    except Exception:
        pass
    if not items:
        return (
            f"📊 <b>Hive RNS Daily Digest</b>\n"
            f"{escape(day_label)} · UK AIM micro-cap universe\n\n"
            f"No new RNS for the universe today."
        )
    tickers = Counter(i["ticker"] for i in items if i.get("ticker") and i["ticker"] != "—")
    price = [i for i in items if i["bucket"] == "price"]
    holding = [i for i in items if i["bucket"] == "holding"]
    other = [i for i in items if i["bucket"] == "other"]
    lines = [
        "📊 <b>Hive RNS Daily Digest</b>",
        f"{escape(day_label)} · UK AIM micro-cap universe",
        "",
        f"<b>{len(items)}</b> RNS logged · <b>{len(tickers)}</b> tickers",
        "━━━━━━━━━━━━━━━━",
    ]

    def add_section(emoji, label, rows, with_summary=True, limit=8):
        if not rows:
            return
        lines.append(f"{emoji} <b>{label}</b>")
        for i in rows[:limit]:
            t = escape(i["ticker"])
            title = escape((i["title"] or "")[:80])
            lines.append(f"• <b>#{t}</b> — {title}")
            if with_summary:
                s = _short_ai(i.get("summary") or "")
                if s:
                    lines.append(f"  🧠 <i>{escape(s)}</i>")
            if i.get("link"):
                lines.append(f'  🔗 <a href="{escape(i["link"])}">Full RNS</a>')
        if len(rows) > limit:
            lines.append(f"  <i>… +{len(rows) - limit} more</i>")
        lines.append("━━━━━━━━━━━━━━━━")

    add_section("🔴", "Operational / capital / price-relevant", price, True, 8)
    add_section("🟡", "Holdings / equity admin", holding, False, 12)
    add_section("⚪", "Other", other, False, 6)
    if tickers:
        top = ", ".join(f"#{escape(t)} ({n})" for t, n in tickers.most_common(5))
        lines.append(f"📈 <b>Most active</b>: {top}")
    lines.append("")
    lines.append("Full detail → Notion · Hive RNS News Log")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>…truncated</i>"
    return text


def _digest_already_sent(date_iso: str) -> bool:
    try:
        if os.path.exists(DIGEST_STATE_FILE):
            with open(DIGEST_STATE_FILE) as f:
                return f.read().strip() == date_iso
    except Exception:
        pass
    return False


def _mark_digest_sent(date_iso: str) -> None:
    try:
        parent = os.path.dirname(DIGEST_STATE_FILE)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(DIGEST_STATE_FILE, "w") as f:
            f.write(date_iso)
    except Exception as e:
        print(f"digest state write failed: {e}")


def send_daily_digest(force: bool = False) -> bool:
    """Post today's digest to NOTIFICATION_CHAT_ID once per weekday after DIGEST_HOUR:MINUTE London."""
    if not DIGEST_ENABLED and not force:
        return False

    token = os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("NOTIFICATION_CHAT_ID")
    notion_token = os.getenv("NOTION_TOKEN")
    rns_db = os.getenv("NOTION_RNS_DB_ID") or "a7931699-9ab9-4fe6-8a81-74d86146ae1a"
    if not token or not chat:
        print("digest: TOKEN or NOTIFICATION_CHAT_ID missing")
        return False

    try:
        from zoneinfo import ZoneInfo

        london = datetime.now(ZoneInfo("Europe/London"))
    except Exception:
        london = datetime.now(timezone.utc)

    date_iso = london.strftime("%Y-%m-%d")
    if not force:
        if london.weekday() >= 5:
            return False
        mins = london.hour * 60 + london.minute
        if mins < DIGEST_HOUR * 60 + DIGEST_MINUTE:
            return False
        if _digest_already_sent(date_iso):
            return False

    headers = {}
    if notion_token:
        headers = {
            "Authorization": f"Bearer {notion_token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
    items = fetch_rns_for_date(date_iso, notion_token or "", rns_db, headers)
    seen = set()
    unique = []
    for it in items:
        key = (it["ticker"], it["title"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    msg = format_daily_digest(date_iso, unique)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        res = requests.post(url, json=payload, timeout=20)
        if res.status_code == 200:
            _mark_digest_sent(date_iso)
            print(f"digest: sent {len(unique)} items for {date_iso}")
            return True
        print(f"digest send failed {res.status_code}: {res.text[:200]}")
    except Exception as e:
        print(f"digest send error: {e}")
    return False
