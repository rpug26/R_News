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

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTIFICATION_CHAT_ID = os.getenv("NOTIFICATION_CHAT_ID")
LOG_CHAT_ID = os.getenv("LOG_CHAT_ID")
FILE_NAME = os.getenv("RNS_STATE_FILE", "last_rns_ids.txt")
TICKER_FILE = "tickers.txt"  # fallback only

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_TICKERS_DB_ID = os.getenv("NOTION_TICKERS_DB_ID")  # UK AIM Micro-Cap database
NOTION_WATCHLIST_DB_ID = os.getenv("NOTION_WATCHLIST_DB_ID")  # Hive My Watchlist DB
NOTION_RNS_DB_ID = os.getenv("NOTION_RNS_DB_ID")  # RNS News Log database

NOTION_HEADERS = None
if NOTION_TOKEN:
    NOTION_HEADERS = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def log_to_telegram(message):
    """Prints to console and sends a log to the dedicated Telegram channel."""
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


def load_tickers_from_notion():
    # type: () -> List[str]
    """Pull all tickers from the UK AIM Micro-Cap Notion database."""
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
                print(f"Notion query error {res.status_code}: {res.text[:300]}")
                break

            data = res.json()
            for page in data.get("results", []):
                props = page.get("properties", {})
                title_prop = props.get("Ticker") or {}
                title_list = title_prop.get("title") or []
                if title_list:
                    t = title_list[0].get("plain_text", "").strip().upper()
                    if t:
                        tickers.append(t)

            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        except Exception as e:
            print(f"Notion load error: {e}")
            break

    return sorted(set(tickers))


def load_tickers():
    # type: () -> List[str]
    """Prefer Notion; fall back to local tickers.txt."""
    notion_tickers = load_tickers_from_notion()
    if notion_tickers:
        print(f"Loaded {len(notion_tickers)} tickers from Notion.")
        return notion_tickers

    if os.path.exists(TICKER_FILE):
        with open(TICKER_FILE, "r") as f:
            lines = [line.strip().upper() for line in f if line.strip()]
            print(f"Fallback: loaded {len(lines)} tickers from tickers.txt")
            return lines
    return []


def find_notion_page_id(ticker):
    # type: (str) -> Optional[str]
    """Return the Notion page_id for a given ticker, or None."""
    if not NOTION_TOKEN or not NOTION_TICKERS_DB_ID:
        return None

    db_id = _normalize_uuid(NOTION_TICKERS_DB_ID)
    ticker = ticker.upper().strip()
    filters = [
        {"property": "Ticker", "title": {"equals": ticker}},
        {"property": "Ticker", "rich_text": {"equals": ticker}},
    ]

    for f in filters:
        try:
            res = requests.post(
                f"https://api.notion.com/v1/databases/{db_id}/query",
                headers=NOTION_HEADERS,
                json={"filter": f, "page_size": 1},
                timeout=15,
            )
            if res.status_code == 200:
                results = res.json().get("results", [])
                if results:
                    return results[0]["id"]
        except Exception as e:
            print(f"find_notion_page_id error for {ticker}: {e}")
    return None


def update_last_rns_date(page_id, rns_date_iso):
    # type: (str, str) -> bool
    """Write the Last RNS Date property on the Notion page."""
    if not NOTION_TOKEN or not page_id:
        return False

    payload = {
        "properties": {
            "Last RNS Date": {
                "date": {"start": rns_date_iso}
            }
        }
    }
    try:
        res = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=NOTION_HEADERS,
            json=payload,
            timeout=15,
        )
        if res.status_code == 200:
            return True
        print(f"Notion update failed {res.status_code}: {res.text[:200]}")
        return False
    except Exception as e:
        print(f"Notion update error: {e}")
        return False


def extract_ai_summary(rns_url):
    # type: (str) -> str
    """Fetch Investegate RNS page and extract the Summary by AI block."""
    if not rns_url:
        return ""
    try:
        res = c_requests.get(rns_url, impersonate="safari15_5", timeout=20)
        if res.status_code != 200:
            print(f"  → AI summary fetch failed status {res.status_code}")
            return ""
        soup = BeautifulSoup(res.text, "html.parser")

        # Prefer explicit heading match
        summary_parts = []
        for tag in soup.find_all(["h2", "h3", "h4", "div", "section", "p"]):
            text = tag.get_text(" ", strip=True)
            if re.search(r"summary\s+by\s+ai", text, re.I):
                # Collect following sibling text blocks
                for sib in tag.find_all_next(["p", "div"], limit=12):
                    t = sib.get_text(" ", strip=True)
                    if not t:
                        continue
                    if re.search(r"disclaimer|this information is provided by rns", t, re.I):
                        break
                    if re.search(r"summary\s+by\s+ai", t, re.I):
                        continue
                    # Stop when we hit the formal company header / date block
                    if re.match(r"^\d{1,2}\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$", t):
                        break
                    if len(t) > 40:
                        summary_parts.append(t)
                    if sum(len(x) for x in summary_parts) > 1200:
                        break
                break

        if not summary_parts:
            # Fallback: look for class names that often wrap AI summary
            for cls in ["ai-summary", "summary-by-ai", "aiSummary", "summary"]:
                node = soup.find(class_=re.compile(cls, re.I))
                if node:
                    t = node.get_text(" ", strip=True)
                    t = re.sub(r"(?i)summary\s+by\s+ai\s*(beta)?\s*(close)?\s*x?", "", t).strip()
                    if len(t) > 40:
                        summary_parts.append(t)
                        break

        summary = " ".join(summary_parts).strip()
        summary = re.sub(r"\s+", " ", summary)
        if len(summary) > 1500:
            summary = summary[:1497] + "..."
        return summary
    except Exception as e:
        print(f"  → extract_ai_summary error: {e}")
        return ""


def create_rns_log_entry(ticker, company, title, rns_date_iso, link, ai_summary, rns_hash):
    # type: (str, str, str, str, str, str, str) -> bool
    """Create one row in the RNS News Log database."""
    if not NOTION_TOKEN or not NOTION_RNS_DB_ID:
        print("  → NOTION_RNS_DB_ID not set; skip RNS log")
        return False

    db_id = _normalize_uuid(NOTION_RNS_DB_ID)
    props = {
        "Title": {"title": [{"text": {"content": (title or "RNS")[:2000]}}]},
        "Ticker": {"rich_text": [{"text": {"content": (ticker or "")[:200]}}]},
        "Company": {"rich_text": [{"text": {"content": (company or "")[:500]}}]},
        "Link": {"url": link if link and link.startswith("http") else None},
        "AI Summary": {"rich_text": [{"text": {"content": (ai_summary or "")[:2000]}}]},
        "RNS Hash": {"rich_text": [{"text": {"content": (rns_hash or "")[:100]}}]},
        "Source": {"select": {"name": "Investegate"}},
    }
    if rns_date_iso:
        props["RNS Date"] = {"date": {"start": rns_date_iso}}

    # Notion rejects null URL — drop if empty
    if not props["Link"]["url"]:
        del props["Link"]

    payload = {"parent": {"database_id": db_id}, "properties": props}
    try:
        res = requests.post(
            "https://api.notion.com/v1/pages",
            headers=NOTION_HEADERS,
            json=payload,
            timeout=20,
        )
        if res.status_code in (200, 201):
            return True
        print(f"  → RNS log create failed {res.status_code}: {res.text[:250]}")
        return False
    except Exception as e:
        print(f"  → RNS log create error: {e}")
        return False


def _get_page_rich_text(page_id, prop_name):
    # type: (str, str) -> str
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
    # type: (str, str, str, str) -> bool
    """Prepend latest RNS into Last 3 RNS (keep max 3 blocks)."""
    if not NOTION_TOKEN or not page_id:
        return False

    snippet = (ai_summary or title or "").strip()
    if len(snippet) > 280:
        snippet = snippet[:277] + "..."
    block = f"• {rns_date_iso} | {title}\n  {snippet}".strip()

    existing = _get_page_rich_text(page_id, "Last 3 RNS")
    # Split previous blocks on leading bullet lines
    parts = []
    if existing:
        # Split on newline before bullet
        raw_parts = re.split(r"\n(?=• )", existing)
        for p in raw_parts:
            p = p.strip()
            if p:
                parts.append(p)

    parts = [block] + [p for p in parts if p and not p.startswith(f"• {rns_date_iso} | {title}")]
    parts = parts[:3]
    new_text = "\n\n".join(parts)
    if len(new_text) > 1900:
        new_text = new_text[:1897] + "..."

    payload = {
        "properties": {
            "Last 3 RNS": {
                "rich_text": [{"text": {"content": new_text}}]
            }
        }
    }
    try:
        res = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=NOTION_HEADERS,
            json=payload,
            timeout=15,
        )
        if res.status_code == 200:
            return True
        print(f"  → Last 3 RNS update failed {res.status_code}: {res.text[:200]}")
        return False
    except Exception as e:
        print(f"  → Last 3 RNS update error: {e}")
        return False


def send_telegram_msg(text, rns_url=None, max_retries=3, chat_id=None):
    """Send to notification channel (default) or a specific chat_id (DM)."""
    target = chat_id or NOTIFICATION_CHAT_ID
    if not target or not TOKEN:
        print("Error: chat_id/NOTIFICATION_CHAT_ID or TOKEN not set.")
        return False

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": target,
        "text": text,
        "parse_mode": "HTML",
    }
    if rns_url:
        payload["link_preview_options"] = {
            "url": rns_url,
            "is_disabled": False,
            "prefer_large_media": False,
            "show_above_text": False,
        }

    for attempt in range(max_retries):
        try:
            res = requests.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                return True
            elif res.status_code == 429:
                error_data = res.json()
                retry_after = error_data.get("parameters", {}).get("retry_after", 30)
                log_to_telegram(f"⚠️ Rate limited by Telegram! Pausing for {retry_after}s...")
                time.sleep(retry_after)
            else:
                print(f"Telegram API Error ({target}): {res.text[:200]}")
                break
        except Exception as e:
            print(f"Telegram connection error: {e}")
            time.sleep(5)
    print("Failed to send message after maximum retries.")
    return False


def find_watchlist_user_ids(ticker):
    # type: (str) -> List[int]
    """Return unique Telegram user IDs who have this ticker on My Watchlist."""
    if not NOTION_TOKEN or not NOTION_WATCHLIST_DB_ID:
        return []

    db_id = _normalize_uuid(NOTION_WATCHLIST_DB_ID)
    ticker = ticker.upper().strip()
    user_ids = set()
    has_more = True
    start_cursor = None

    while has_more:
        body = {
            "page_size": 100,
            "filter": {
                "or": [
                    {"property": "Ticker", "title": {"equals": ticker}},
                    {"property": "Ticker", "rich_text": {"equals": ticker}},
                ]
            },
        }
        if start_cursor:
            body["start_cursor"] = start_cursor
        try:
            res = requests.post(
                f"https://api.notion.com/v1/databases/{db_id}/query",
                headers=NOTION_HEADERS,
                json=body,
                timeout=20,
            )
            if res.status_code != 200:
                print(f"Watchlist query error {res.status_code}: {res.text[:200]}")
                break
            data = res.json()
            for page in data.get("results", []):
                props = page.get("properties", {})
                uid_prop = props.get("Telegram User ID") or {}
                uid_text = ""
                if uid_prop.get("type") == "rich_text":
                    parts = uid_prop.get("rich_text") or []
                    if parts:
                        uid_text = parts[0].get("plain_text", "")
                elif uid_prop.get("type") == "title":
                    parts = uid_prop.get("title") or []
                    if parts:
                        uid_text = parts[0].get("plain_text", "")
                uid_text = (uid_text or "").strip()
                if uid_text.isdigit():
                    user_ids.add(int(uid_text))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        except Exception as e:
            print(f"find_watchlist_user_ids error: {e}")
            break

    return sorted(user_ids)


def notify_watchlist_holders(ticker, text, rns_url=None):
    # type: (str, str, Optional[str]) -> int
    """DM each user who has ticker on My Watchlist. Returns number notified."""
    user_ids = find_watchlist_user_ids(ticker)
    if not user_ids:
        print(f"  → No watchlist holders for {ticker}")
        return 0
    personal = f"👀 <b>My Watchlist alert</b> · #{ticker}\n\n" + text
    sent = 0
    for uid in user_ids:
        ok = send_telegram_msg(personal, rns_url=rns_url, chat_id=uid)
        if ok:
            sent += 1
            print(f"  → Watchlist DM sent to {uid}")
        else:
            print(f"  → Watchlist DM failed for {uid}")
        time.sleep(0.4)
    return sent


def check_rns():
    tickers = load_tickers()
    if not tickers:
        log_to_telegram("Watchlist is empty. No tickers to scan.")
        return
    print(f"Starting scan for {len(tickers)} tickers.")

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
        max_scrape_retries = 3
        table = None
        response_status = None

        for attempt in range(max_scrape_retries):
            try:
                response = c_requests.get(today_url, impersonate="safari15_5", timeout=15)
                response_status = response.status_code
                if response_status == 200:
                    soup = BeautifulSoup(response.text, "html.parser")
                    table = soup.find("table")
                    if table:
                        break
                print(f"⚠️ Scrape attempt {attempt + 1} failed (Status: {response_status}). Retrying...")
                time.sleep(5)
            except Exception as e:
                print(f"⚠️ Network error on attempt {attempt + 1}: {e}")
                time.sleep(5)

        if not table:
            print(f"❌ Could not find announcements table after {max_scrape_retries} attempts.")
            return

        rows = table.find_all("tr")
        news_found = 0
        batched_log_entries = []
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 4:
                continue

            rns_time = cols[0].get_text().strip()
            company_raw = cols[2].get_text().upper()
            announcement_cell = cols[3]

            for ticker in tickers:
                if re.search(rf"\({re.escape(ticker)}\)", company_raw):
                    link_tag = announcement_cell.find("a", href=True)
                    if not link_tag:
                        continue

                    title = link_tag.get_text().strip()
                    full_link = urljoin(base_url, link_tag["href"]).strip().rstrip("?")

                    unique_string = f"{rns_time}_{ticker}_{title}_{full_link}"
                    rns_id = hashlib.md5(unique_string.encode()).hexdigest()

                    if rns_id not in last_seen_hashes:
                        clean_company = company_raw.split("(")[0].replace("\n", " ").strip()
                        clean_company = re.sub(" +", " ", clean_company)

                        print(f"[{rns_time}] MATCH: {ticker} | Hash: {rns_id[:12]}")
                        batched_log_entries.append(f"• [{rns_time}] <b>{ticker}</b> - {clean_company}")

                        # Extract AI summary from full RNS page
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

                        # --- My Watchlist personal alerts ---
                        try:
                            n = notify_watchlist_holders(
                                ticker, msg, rns_url=preview_url
                            )
                            if n:
                                print(f"  → Notified {n} watchlist holder(s) for {ticker}")
                        except Exception as we:
                            print(f"  → Watchlist notify error: {we}")

                        # --- Notion: RNS News Log + Last RNS Date + Last 3 RNS ---
                        try:
                            create_rns_log_entry(
                                ticker=ticker,
                                company=clean_company,
                                title=title,
                                rns_date_iso=today_iso,
                                link=full_link,
                                ai_summary=ai_summary,
                                rns_hash=rns_id,
                            )
                        except Exception as le:
                            print(f"  → RNS log error: {le}")

                        page_id = find_notion_page_id(ticker)
                        if page_id:
                            ok = update_last_rns_date(page_id, today_iso)
                            if ok:
                                print(f"  → Notion Last RNS Date updated for {ticker}")
                            else:
                                print(f"  → Notion Last RNS Date failed for {ticker}")

                            ok3 = update_last_3_rns(page_id, today_iso, title, ai_summary)
                            if ok3:
                                print(f"  → Notion Last 3 RNS updated for {ticker}")
                            else:
                                print(f"  → Notion Last 3 RNS failed for {ticker}")
                        else:
                            print(f"  → No Notion page found for {ticker}")

                        time.sleep(1)

                        log_entry = f"{rns_time} | {ticker} | {rns_id}"
                        with open(FILE_NAME, "a") as f:
                            f.write(log_entry + "\n")
                        last_seen_hashes.add(rns_id)
                        news_found += 1

                    break  # next row

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
