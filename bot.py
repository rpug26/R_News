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

def log_to_telegram(message):
    """Prints to console and sends a log to the dedicated Telegram channel."""
    print(message)
    if not LOG_CHAT_ID or not TOKEN:
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": LOG_CHAT_ID,
        "text": f"🤖 <b>Bot Log:</b>\n{message}",
        "parse_mode": "HTML"
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
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

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
                headers=headers,
                json=body,
                timeout=30,
            )
            if res.status_code != 200:
                print(f"Notion query error {res.status_code}: {res.text[:300]}")
                break

            data = res.json()
            for page in data.get("results", []):
                props = page.get("properties", {})
                # Ticker is the title property
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
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    ticker = ticker.upper().strip()
    filters = [
        {"property": "Ticker", "title": {"equals": ticker}},
        {"property": "Ticker", "rich_text": {"equals": ticker}},
    ]

    for f in filters:
        try:
            res = requests.post(
                f"https://api.notion.com/v1/databases/{db_id}/query",
                headers=headers,
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

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
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
            headers=headers,
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

def send_telegram_msg(text, rns_url=None, max_retries=3):
    if not NOTIFICATION_CHAT_ID or not TOKEN:
        print("Error: NOTIFICATION_CHAT_ID or TOKEN not set.")
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": NOTIFICATION_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {
            "url": rns_url,
            "is_disabled": False,
            "prefer_large_media": False,
            "show_above_text": False
        }
    }

    for attempt in range(max_retries):
        try:
            res = requests.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                return
            elif res.status_code == 429:
                error_data = res.json()
                retry_after = error_data.get("parameters", {}).get("retry_after", 30)
                log_to_telegram(f"⚠️ Rate limited by Telegram! Pausing for {retry_after}s...")
                time.sleep(retry_after)
            else:
                print(f"Telegram API Error: {res.text}")
                break
        except Exception as e:
            print(f"Telegram connection error: {e}")
            time.sleep(5)
    print("Failed to send message after maximum retries.")

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

                        msg = (
                            f"🕒 <b>{rns_time}</b>\n"
                            f"📰 <b>#{ticker} - {clean_company}</b>\n"
                            f"{title}\n\n"
                            f"🔗 <a href='{full_link}'>Read Full Release</a>"
                        )
                        preview_url = f"{full_link}?t={int(time.time())}"
                        send_telegram_msg(msg, rns_url=preview_url)

                        # --- Notion write-back ---
                        page_id = find_notion_page_id(ticker)
                        if page_id:
                            ok = update_last_rns_date(page_id, today_iso)
                            if ok:
                                print(f"  → Notion Last RNS Date updated for {ticker}")
                            else:
                                print(f"  → Notion update failed for {ticker}")
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
