import requests
from curl_cffi import requests as c_requests
from bs4 import BeautifulSoup
import os
import hashlib
import re
from urllib.parse import urljoin
import time
from datetime import datetime, timezone
from typing import List, Optional
from html import unescape

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTIFICATION_CHAT_ID = os.getenv("NOTIFICATION_CHAT_ID")
LOG_CHAT_ID = os.getenv("LOG_CHAT_ID")
FILE_NAME = os.getenv("RNS_STATE_FILE", "last_rns_ids.txt")
TICKER_FILE = "tickers.txt"

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_TICKERS_DB_ID = os.getenv("NOTION_TICKERS_DB_ID")
NOTION_WATCHLIST_DB_ID = os.getenv("NOTION_WATCHLIST_DB_ID")
NOTION_RNS_DB_ID = (
    os.getenv("NOTION_RNS_DB_ID")
    or "a7931699-9ab9-4fe6-8a81-74d86146ae1a"
).strip()

NOTION_HEADERS = None
if NOTION_TOKEN:
    NOTION_HEADERS = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def log_to_telegram(message):
    print(message)
    if not LOG_CHAT_ID or not TOKEN:
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": LOG_CHAT_ID,
        "text": f"🤖 <b>Bot Log:</b>\n{message}",
        "parse_mode": "HTML",
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Failed to send log to Telegram: {e}")


def _normalize_uuid(raw):
    raw = (raw or "").replace("-", "").strip()
    if len(raw) == 32:
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    return raw


def _plain_from_prop(prop):
    if not prop or not isinstance(prop, dict):
        return ""
    t = prop.get("type")
    if t == "title":
        return "".join(x.get("plain_text", "") for x in prop.get("title", [])).strip()
    if t == "rich_text":
        return "".join(x.get("plain_text", "") for x in prop.get("rich_text", [])).strip()
    if t == "select":
        sel = prop.get("select") or {}
        return (sel.get("name") or "").strip()
    if t == "url":
        return (prop.get("url") or "").strip()
    return ""


def load_tickers_from_notion():
    if not NOTION_TOKEN or not NOTION_TICKERS_DB_ID:
        return []
    db_id = _normalize_uuid(NOTION_TICKERS_DB_ID)
    tickers = []
    has_more = True
    start_cursor = None
    while has_more:
        body = {"page_size": 100}
        if start_cursor:
            body["start_cursor"] = start_cursor
        try:
            res = requests.post(
                f"https://api.notion.com/v1/databases/{db_id}/query",
                headers=NOTION_HEADERS,
                json=body,
                timeout=30,
            )
            if res.status_code != 200:
                print(f"Notion ticker query failed {res.status_code}: {res.text[:200]}")
                break
            data = res.json()
            for page in data.get("results", []):
                props = page.get("properties", {})
                t = _plain_from_prop(props.get("Ticker")).upper().strip().lstrip("#")
                if t and re.fullmatch(r"[A-Z0-9]{1,6}", t) and t not in tickers:
                    tickers.append(t)
            has_more = bool(data.get("has_more"))
            start_cursor = data.get("next_cursor")
        except Exception as e:
            print(f"load_tickers_from_notion error: {e}")
            break
    return tickers


def load_tickers():
    notion_tickers = load_tickers_from_notion()
    if notion_tickers:
        print(f"Loaded {len(notion_tickers)} tickers from Notion.")
        return notion_tickers
    if os.path.exists(TICKER_FILE):
        with open(TICKER_FILE) as f:
            lines = [ln.strip().upper() for ln in f if ln.strip() and not ln.startswith("#")]
        print(f"Loaded {len(lines)} tickers from {TICKER_FILE}.")
        return lines
    print("No tickers loaded.")
    return []


def find_notion_page_id(ticker):
    if not NOTION_TOKEN or not NOTION_TICKERS_DB_ID or not ticker:
        return None
    db_id = _normalize_uuid(NOTION_TICKERS_DB_ID)
    ticker = ticker.upper().strip()
    for filt in [
        {"property": "Ticker", "title": {"equals": ticker}},
        {"property": "Ticker", "rich_text": {"equals": ticker}},
    ]:
        try:
            res = requests.post(
                f"https://api.notion.com/v1/databases/{db_id}/query",
                headers=NOTION_HEADERS,
                json={"filter": filt, "page_size": 1},
                timeout=15,
            )
            if res.status_code != 200:
                continue
            results = res.json().get("results") or []
            if results:
                return results[0]["id"]
        except Exception as e:
            print(f"find_notion_page_id error for {ticker}: {e}")
    return None


def update_last_rns_date(page_id, iso_date):
    if not NOTION_TOKEN or not page_id or not iso_date:
        return False
    payload = {"properties": {"Last RNS Date": {"date": {"start": iso_date[:10]}}}}
    try:
        res = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=NOTION_HEADERS,
            json=payload,
            timeout=15,
        )
        return res.status_code == 200
    except Exception as e:
        print(f"  → Last RNS Date error: {e}")
        return False


def rns_log_exists(rns_hash):
    if not NOTION_TOKEN or not NOTION_RNS_DB_ID or not rns_hash:
        return False
    db_id = _normalize_uuid(NOTION_RNS_DB_ID)
    try:
        res = requests.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=NOTION_HEADERS,
            json={
                "page_size": 1,
                "filter": {
                    "property": "RNS Hash",
                    "rich_text": {"equals": rns_hash[:100]},
                },
            },
            timeout=15,
        )
        if res.status_code != 200:
            return False
        return bool(res.json().get("results"))
    except Exception as e:
        print(f"  → rns_log_exists error: {e}")
        return False


def create_rns_log_entry(ticker, company, title, rns_date_iso, link, ai_summary, rns_hash):
    """Write one row into Hive RNS News Log, matched to ticker."""
    if not NOTION_TOKEN:
        print("  → NOTION_TOKEN missing; skip RNS log")
        return False
    if not NOTION_RNS_DB_ID:
        print("  → NOTION_RNS_DB_ID not set; skip RNS log")
        return False
    if rns_hash and rns_log_exists(rns_hash):
        print(f"  → RNS log already has hash {rns_hash[:12]}… – skip")
        return True
    db_id = _normalize_uuid(NOTION_RNS_DB_ID)
    props = {
        "Title": {"title": [{"text": {"content": (title or "RNS")[:2000]}}]},
        "Ticker": {"rich_text": [{"text": {"content": (ticker or "").upper()[:200]}}]},
        "Company": {"rich_text": [{"text": {"content": (company or "")[:500]}}]},
        "AI Summary": {"rich_text": [{"text": {"content": (ai_summary or "")[:2000]}}]},
        "RNS Hash": {"rich_text": [{"text": {"content": (rns_hash or "")[:100]}}]},
        "Source": {"select": {"name": "Investegate"}},
    }
    if link and str(link).startswith("http"):
        props["Link"] = {"url": link}
    if rns_date_iso:
        props["RNS Date"] = {"date": {"start": rns_date_iso[:10]}}
    payload = {"parent": {"database_id": db_id}, "properties": props}
    try:
        res = requests.post(
            "https://api.notion.com/v1/pages",
            headers=NOTION_HEADERS,
            json=payload,
            timeout=20,
        )
        if res.status_code in (200, 201):
            print(f"  → Hive RNS News Log: saved #{(ticker or '').upper()} | {(title or '')[:60]}")
            return True
        print(f"  → RNS log create failed {res.status_code}: {res.text[:300]}")
        return False
    except Exception as e:
        print(f"  → RNS log create error: {e}")
        return False


def extract_tickers_from_company_cell(company_raw, known_tickers):
    raw = (company_raw or "").upper()
    found = []
    for m in re.finditer(r"\(([A-Z0-9]{1,6})\)", raw):
        t = m.group(1)
        if t in known_tickers and t not in found:
            found.append(t)
    if found:
        return found
    for t in known_tickers:
        if re.search(rf"\b{re.escape(t)}\b", raw) and t not in found:
            found.append(t)
    return found


def _get_page_rich_text(page_id, prop_name):
    try:
        res = requests.get(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=NOTION_HEADERS,
            timeout=15,
        )
        if res.status_code != 200:
            return ""
        props = res.json().get("properties", {})
        prop = props.get(prop_name) or {}
        chunks = prop.get("rich_text") or []
        return "".join(c.get("plain_text", "") for c in chunks).strip()
    except Exception as e:
        print(f"  → read {prop_name} error: {e}")
        return ""


def update_last_3_rns(page_id, rns_date_iso, title, ai_summary):
    if not NOTION_TOKEN or not page_id:
        return False
    snippet = (ai_summary or title or "").strip()
    if len(snippet) > 280:
        snippet = snippet[:277] + "..."
    block = f"• {rns_date_iso} | {title}\n  {snippet}".strip()
    existing = _get_page_rich_text(page_id, "Last 3 RNS")
    parts = []
    if existing:
        for p in re.split(r"\n(?=• )", existing):
            p = p.strip()
            if p:
                parts.append(p)
    parts = [block] + [p for p in parts if p and not p.startswith(f"• {rns_date_iso} | {title}")]
    parts = parts[:3]
    new_text = "\n\n".join(parts)
    if len(new_text) > 1900:
        new_text = new_text[:1897] + "..."
    payload = {"properties": {"Last 3 RNS": {"rich_text": [{"text": {"content": new_text}}]}}}
    try:
        res = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=NOTION_HEADERS,
            json=payload,
            timeout=15,
        )
        return res.status_code == 200
    except Exception as e:
        print(f"  → Last 3 RNS update error: {e}")
        return False


def send_telegram_msg(text, rns_url=None, max_retries=3, chat_id=None):
    target = chat_id or NOTIFICATION_CHAT_ID
    if not target or not TOKEN:
        print("Error: chat_id/NOTIFICATION_CHAT_ID or TOKEN not set.")
        return False
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": target, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}
    for attempt in range(max_retries):
        try:
            res = requests.post(url, json=payload, timeout=15)
            if res.status_code == 200:
                return True
            print(f"Telegram send failed {res.status_code}: {res.text[:150]}")
        except Exception as e:
            print(f"Telegram send error: {e}")
        time.sleep(1)
    return False


def extract_ai_summary(rns_url):
    if not rns_url:
        return ""
    try:
        res = c_requests.get(rns_url, impersonate="safari15_5", timeout=20)
        if res.status_code != 200:
            return ""
        soup = BeautifulSoup(res.text, "html.parser")
        node = soup.find(id="collapseSummary")
        if not node:
            node = soup.find(class_=re.compile(r"summary", re.I))
        if not node:
            return ""
        text = node.get_text(" ", strip=True)
        text = unescape(re.sub(r"\s+", " ", text)).strip()
        return text[:2000]
    except Exception as e:
        print(f"  → AI summary scrape error: {e}")
        return ""


def notify_watchlist_holders(ticker, text, rns_url=None):
    if not NOTION_TOKEN or not NOTION_WATCHLIST_DB_ID or not ticker:
        return 0
    db_id = _normalize_uuid(NOTION_WATCHLIST_DB_ID)
    user_ids = set()
    try:
        cursor = None
        while True:
            body = {
                "page_size": 100,
                "filter": {
                    "or": [
                        {"property": "Ticker", "title": {"equals": ticker}},
                        {"property": "Ticker", "rich_text": {"equals": ticker}},
                    ]
                },
            }
            if cursor:
                body["start_cursor"] = cursor
            res = requests.post(
                f"https://api.notion.com/v1/databases/{db_id}/query",
                headers=NOTION_HEADERS,
                json=body,
                timeout=20,
            )
            if res.status_code != 200:
                break
            data = res.json()
            for page in data.get("results", []):
                props = page.get("properties", {})
                uid = _plain_from_prop(props.get("Telegram User ID")).strip()
                if uid.isdigit():
                    user_ids.add(int(uid))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
            if not cursor:
                break
    except Exception as e:
        print(f"  → watchlist query error: {e}")
        return 0
    if not user_ids:
        return 0
    personal = f"👀 <b>My Watchlist alert</b> · #{ticker}\n\n" + text
    sent = 0
    for uid in user_ids:
        ok = send_telegram_msg(personal, rns_url=rns_url, chat_id=uid)
        if ok:
            sent += 1
            print(f"  → Watchlist DM sent to {uid}")
        time.sleep(0.4)
    return sent


def check_rns():
    tickers = load_tickers()
    if not tickers:
        log_to_telegram("Watchlist is empty. No tickers to scan.")
        return
    print(f"Starting scan for {len(tickers)} tickers.")
    if NOTION_TOKEN and NOTION_RNS_DB_ID:
        print(f"Hive RNS News Log target: {NOTION_RNS_DB_ID[:8]}…")
    else:
        print("WARNING: Notion RNS log not fully configured (token/db id)")

    base_url = "https://www.investegate.co.uk"
    today_url = urljoin(base_url, "/today-announcements/?perPage=300")
    last_seen_hashes = set()
    if os.path.exists(FILE_NAME):
        with open(FILE_NAME, "r") as f:
            for line in f:
                parts = line.strip().split(" | ")
                if parts:
                    last_seen_hashes.add(parts[-1])

    try:
        table = None
        for attempt in range(3):
            try:
                response = c_requests.get(today_url, impersonate="safari15_5", timeout=15)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")
                    table = soup.find("table")
                    if table:
                        break
                print(f"⚠️ Scrape attempt {attempt + 1} failed. Retrying...")
                time.sleep(5)
            except Exception as e:
                print(f"⚠️ Network error on attempt {attempt + 1}: {e}")
                time.sleep(5)

        if not table:
            print("❌ Could not find announcements table after 3 attempts.")
            return

        rows = table.find_all("tr")
        news_found = 0
        batched_log_entries = []
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        known = set(tickers)

        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 4:
                continue
            rns_time = cols[0].get_text().strip()
            company_raw = cols[2].get_text().upper()
            announcement_cell = cols[3]
            matched = extract_tickers_from_company_cell(company_raw, list(known))
            if not matched:
                continue
            link_tag = announcement_cell.find("a", href=True)
            if not link_tag:
                continue
            title = link_tag.get_text().strip()
            full_link = urljoin(base_url, link_tag["href"]).strip().rstrip("?")
            clean_company = company_raw.split("(")[0].replace("\n", " ").strip()
            clean_company = re.sub(" +", " ", clean_company)

            for ticker in matched:
                unique_string = f"{rns_time}_{ticker}_{title}_{full_link}"
                rns_id = hashlib.md5(unique_string.encode()).hexdigest()
                if rns_id in last_seen_hashes:
                    continue

                print(f"[{rns_time}] MATCH: {ticker} | Hash: {rns_id[:12]}")
                batched_log_entries.append(f"• [{rns_time}] <b>{ticker}</b> - {clean_company}")

                ai_summary = extract_ai_summary(full_link)
                if ai_summary:
                    print(f"  → AI summary: {ai_summary[:120]}...")
                else:
                    print("  → No AI summary found on page")

                msg = (
                    f"🕒 <b>{rns_time}</b>\n"
                    f"📰 <b>#{ticker} - {clean_company}</b>\n"
                    f"{title}\n"
                )
                if ai_summary:
                    short = ai_summary if len(ai_summary) <= 500 else ai_summary[:497] + "..."
                    msg += f"\n🧠 <i>{short}</i>\n"
                msg += f"\n🔗 <a href='{full_link}'>Read Full Release</a>"

                preview_url = f"{full_link}?t={int(time.time())}"
                send_telegram_msg(msg, rns_url=preview_url)

                try:
                    n = notify_watchlist_holders(ticker, msg, rns_url=preview_url)
                    if n:
                        print(f"  → Notified {n} watchlist holder(s) for {ticker}")
                except Exception as we:
                    print(f"  → Watchlist notify error: {we}")

                try:
                    ok_log = create_rns_log_entry(
                        ticker=ticker,
                        company=clean_company,
                        title=title,
                        rns_date_iso=today_iso,
                        link=full_link,
                        ai_summary=ai_summary,
                        rns_hash=rns_id,
                    )
                    if not ok_log:
                        print(f"  → WARN: RNS News Log write failed for {ticker}")
                except Exception as le:
                    print(f"  → RNS log error: {le}")

                page_id = find_notion_page_id(ticker)
                if page_id:
                    if update_last_rns_date(page_id, today_iso):
                        print(f"  → Notion Last RNS Date updated for {ticker}")
                    if update_last_3_rns(page_id, today_iso, title, ai_summary):
                        print(f"  → Notion Last 3 RNS updated for {ticker}")
                else:
                    print(f"  → No UK AIM Micro-Cap page for {ticker} (log row still saved)")

                time.sleep(1)
                with open(FILE_NAME, "a") as f:
                    f.write(f"{rns_time} | {ticker} | {rns_id}\n")
                last_seen_hashes.add(rns_id)
                news_found += 1

        if news_found > 0:
            summary_msg = f"Found {news_found} new items:\n\n" + "\n".join(batched_log_entries)
            if len(summary_msg) > 4000:
                summary_msg = summary_msg[:4000] + "\n\n<i>... [Log truncated]</i>"
            log_to_telegram(summary_msg)
        else:
            print("Scan complete. No new items.")
    except Exception as e:
        log_to_telegram(f"Scraper Error: {e}")


if __name__ == "__main__":
    check_rns()
