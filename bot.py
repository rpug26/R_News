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
TICKER_FILE = "tickers.txt"  # fallback only

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_TICKERS_DB_ID = os.getenv("NOTION_TICKERS_DB_ID")  # UK AIM Micro-Cap database
NOTION_WATCHLIST_DB_ID = os.getenv("NOTION_WATCHLIST_DB_ID")  # Hive My Watchlist DB
# Hive RNS News Log – default to the live Hive database if env not set
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
                t = _plain_from_prop(props.get("Ticker")).upper().strip()
                if not t:
                    t = _plain_from_prop(props.get("Name")).upper().strip()
                # Normalise #ALRT → ALRT
                t = t.lstrip("#").strip()
                if t and re.fullmatch(r"[A-Z0-9]{1,6}", t):
                    tickers.append(t)

            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        except Exception as e:
            print(f"Notion load error: {e}")
            break

    return sorted(set(tickers))


def _plain_from_prop(prop):
    # type: (Optional[dict]) -> str
    """Extract plain text from a Notion title / rich_text / select property."""
    if not prop or not isinstance(prop, dict):
        return ""
    ptype = prop.get("type")
    if ptype == "title" or "title" in prop:
        chunks = prop.get("title") or []
        return "".join(c.get("plain_text", "") for c in chunks).strip()
    if ptype == "rich_text" or "rich_text" in prop:
        chunks = prop.get("rich_text") or []
        return "".join(c.get("plain_text", "") for c in chunks).strip()
    if ptype == "select" and prop.get("select"):
        return (prop["select"].get("name") or "").strip()
    return ""


def load_tickers():
    # type: () -> List[str]
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
    """Fetch Investegate RNS page and extract the Summary by AI block.

    Investegate puts the AI summary inside <div id="collapseSummary">.
    Older heuristic scraped nav text; this targets the real container.
    """
    if not rns_url:
        return ""
    try:
        res = c_requests.get(rns_url, impersonate="safari15_5", timeout=20)
        if res.status_code != 200:
            print(f"  → AI summary fetch failed status {res.status_code}")
            return ""

        html = res.text

        # Primary: #collapseSummary container
        m = re.search(
            r'id=["\']collapseSummary["\'][^>]*>(.*?)id=["\']summary-disclaimer["\']',
            html,
            flags=re.I | re.S,
        )
        if not m:
            m = re.search(
                r'id=["\']collapseSummary["\'][^>]*>(.*?)</div>\s*</div>\s*</div>',
                html,
                flags=re.I | re.S,
            )

        if m:
            chunk = m.group(1)
            chunk = re.sub(
                r'<p[^>]*id=["\']summary-disclaimer["\'][\s\S]*?</p>',
                " ",
                chunk,
                flags=re.I,
            )
            chunk = re.sub(r"<[^>]+>", " ", chunk)
            chunk = unescape(re.sub(r"\s+", " ", chunk)).strip()
            chunk = re.sub(r"(?i)\bDisclaimer\*?\b.*$", "", chunk).strip()
            # Reject nav-like garbage
            if re.search(r"(?i)advanced search|login register|newswire", chunk):
                chunk = ""
            if len(chunk) > 40:
                if len(chunk) > 1500:
                    chunk = chunk[:1497] + "..."
                return chunk

        # Fallback via BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        node = soup.find(id="collapseSummary")
        if node:
            # remove disclaimer child
            for bad in node.find_all(id="summary-disclaimer"):
                bad.decompose()
            t = node.get_text(" ", strip=True)
            t = re.sub(r"(?i)\bDisclaimer\*?\b.*$", "", t).strip()
            t = re.sub(r"\s+", " ", t)
            if re.search(r"(?i)advanced search|login register|newswire", t):
                return ""
            if len(t) > 40:
                if len(t) > 1500:
                    t = t[:1497] + "..."
                return t

        return ""
    except Exception as e:
        print(f"  → extract_ai_summary error: {e}")
        return ""


def rns_log_exists(rns_hash):
    # type: (str) -> bool
    """True if Hive RNS News Log already has this RNS Hash (dedupe)."""
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
    # type: (str, str, str, str, str, str, str) -> bool
    """
    Write one row into Hive RNS News Log, matched to ticker.
    Schema: Title (title), Ticker, Company, Link, AI Summary, RNS Hash,
            Source (select), RNS Date (date).
    """
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
        "AI Summary": {
            "rich_text": [
                {"text": {"content": (ai_summary or "")[:2000]}}
            ]
        },
        "RNS Hash": {
            "rich_text": [
                {"text": {"content": (rns_hash or "")[:100]}}
            ]
        },
        "Source": {"select": {"name": "Investegate"}},
    }
    if link and str(link).startswith("http"):
        props["Link"] = {"url": link}
    if rns_date_iso:
        # Prefer full ISO date; Notion accepts YYYY-MM-DD
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
            print(
                f"  → Hive RNS News Log: saved #{(ticker or '').upper()} "
                f"| {(title or '')[:60]}"
            )
            return True
        print(f"  → RNS log create failed {res.status_code}: {res.text[:300]}")
        return False
    except Exception as e:
        print(f"  → RNS log create error: {e}")
        return False


def extract_tickers_from_company_cell(company_raw, known_tickers):
    # type: (str, List[str]) -> List[str]
    """
    Match Investegate company cell against known Micro-Cap tickers.
    Primary: (TICKER) in the cell. Fallback: whole-word ticker match.
    """
    raw = (company_raw or "").upper()
    found = []
    # 1) Explicit (ALRT) style
    for m in re.finditer(r"\(([A-Z0-9]{1,6})\)", raw):
        t = m.group(1)
        if t in known_tickers and t not in found:
            found.append(t)
    if found:
        return found
    # 2) Fallback: known ticker as whole token
    for t in known_tickers:
        if re.search(rf"\b{re.escape(t)}\b", raw) and t not in found:
            found.append(t)
    return found


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
    if not NOTION_TOKEN or not page_id:
        return False

    snippet = (ai_summary or title or "").strip()
    if len(snippet) > 280:
        snippet = snippet[:277] + "..."
    block = f"• {rns_date_iso} | {title}\n  {snippet}".strip()

    existing = _get_page_rich_text(page_id, "Last 3 RNS")
    parts = []
    if existing:
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
        day_items = []  # for RNS of the Day one-pager
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
                batched_log_entries.append(
                    f"• [{rns_time}] <b>{ticker}</b> - {clean_company}"
                )

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
                    short = (
                        ai_summary
                        if len(ai_summary) <= 500
                        else ai_summary[:497] + "..."
                    )
                    msg += f"\n🧠 <i>{short}</i>\n"
                msg += f"\n🔗 <a href='{full_link}'>Read Full Release</a>"

                preview_url = f"{full_link}?t={int(time.time())}"
                send_telegram_msg(msg, rns_url=preview_url)

                try:
                    n = notify_watchlist_holders(
                        ticker, msg, rns_url=preview_url
                    )
                    if n:
                        print(
                            f"  → Notified {n} watchlist holder(s) for {ticker}"
                        )
                except Exception as we:
                    print(f"  → Watchlist notify error: {we}")

                # Always write Hive RNS News Log (matched by ticker)
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

                # Mirror onto UK AIM Micro-Cap security page
                page_id = find_notion_page_id(ticker)
                if page_id:
                    ok = update_last_rns_date(page_id, today_iso)
                    if ok:
                        print(f"  → Notion Last RNS Date updated for {ticker}")
                    ok3 = update_last_3_rns(
                        page_id, today_iso, title, ai_summary
                    )
                    if ok3:
                        print(f"  → Notion Last 3 RNS updated for {ticker}")
                else:
                    print(
                        f"  → No UK AIM Micro-Cap page for {ticker} "
                        "(log row still saved)"
                    )

                time.sleep(1)

                log_entry = f"{rns_time} | {ticker} | {rns_id}"
                with open(FILE_NAME, "a") as f:
                    f.write(log_entry + "\n")
                last_seen_hashes.add(rns_id)
                news_found += 1
                day_items.append(
                    {
                        "time": rns_time,
                        "ticker": ticker,
                        "company": clean_company,
                        "title": title,
                        "link": full_link,
                        "ai_summary": ai_summary or "",
                    }
                )

        if news_found > 0:
            # Compact ops log
            summary_msg = (
                f"Found {news_found} new items:\n\n" + "\n".join(batched_log_entries)
            )
            if len(summary_msg) > 4000:
                summary_msg = summary_msg[:4000] + "\n\n<i>... [Log truncated]</i>"
            log_to_telegram(summary_msg)
            # One-pager for channel / easy copy-paste
            try:
                send_rns_of_the_day(day_items, date_label=today_iso)
            except Exception as se:
                print(f"  → RNS of the Day send error: {se}")
        else:
            print("Scan complete. No new items.")

    except Exception as e:
        log_to_telegram(f"Scraper Error: {e}")


def format_rns_of_the_day(items, date_label=None):
    # type: (list, Optional[str]) -> list
    """
    Build Telegram HTML one-pager(s) for RNS of the Day.
    Returns a list of message strings (split under Telegram 4096 limit).
    Easy to copy-paste into any chat.
    """
    if not date_label:
        date_label = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        d = datetime.strptime(date_label[:10], "%Y-%m-%d")
        pretty = d.strftime("%d %b %Y")
    except Exception:
        pretty = date_label

    if not items:
        return [
            f"📋 <b>RNS of the Day</b> — {pretty}\n\n"
            f"<i>No matched AIM Micro-Cap RNS logged for this date.</i>\n\n"
            f"<i>Not financial advice. DYOR.</i>"
        ]

    # Dedupe by ticker+title, keep order
    seen = set()
    unique = []
    for it in items:
        key = (
            (it.get("ticker") or "").upper(),
            (it.get("title") or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    header = (
        f"📋 <b>RNS of the Day</b> — {pretty}\n"
        f"Hive AIM Micro-Cap · {len(unique)} release(s)\n"
        f"{'─' * 22}"
    )
    blocks = []
    for i, it in enumerate(unique, 1):
        ticker = (it.get("ticker") or "").upper()
        company = (it.get("company") or "").strip()
        title = (it.get("title") or "RNS").strip()
        link = (it.get("link") or "").strip()
        t = (it.get("time") or "").strip()
        ai = (it.get("ai_summary") or "").strip()
        if len(ai) > 220:
            ai = ai[:217] + "…"

        line = f"\n\n<b>{i}. #{ticker}</b>"
        if company:
            line += f" — {company}"
        if t:
            line += f"\n🕒 {t}"
        line += f"\n📰 {title}"
        if ai:
            line += f"\n🧠 <i>{ai}</i>"
        if link and link.startswith("http"):
            line += f"\n🔗 <a href='{link}'>Read full RNS</a>"
        blocks.append(line)

    footer = (
        f"\n\n{'─' * 22}\n"
        f"<i>Source: Investegate · Hive RNS News Log\n"
        f"Not financial advice. DYOR.</i>"
    )

    # Pack into Telegram-safe chunks (~3900 chars)
    messages = []
    current = header
    for block in blocks:
        if len(current) + len(block) + len(footer) > 3900:
            messages.append(current + footer)
            current = header + "\n\n<i>(continued)</i>" + block
        else:
            current += block
    messages.append(current + footer)
    return messages


def fetch_todays_rns_from_notion(date_iso=None):
    # type: (Optional[str]) -> list
    """
    Load rows from Hive RNS News Log for a given date (default: today UTC).
    Used by --summary so you can rebuild the one-pager without a live scrape.
    """
    if not NOTION_TOKEN or not NOTION_RNS_DB_ID:
        print("Notion RNS log not configured.")
        return []
    if not date_iso:
        date_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day = date_iso[:10]
    db_id = _normalize_uuid(NOTION_RNS_DB_ID)
    items = []
    try:
        res = requests.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=NOTION_HEADERS,
            json={
                "page_size": 100,
                "filter": {
                    "property": "RNS Date",
                    "date": {"equals": day},
                },
                "sorts": [{"property": "RNS Date", "direction": "descending"}],
            },
            timeout=30,
        )
        if res.status_code != 200:
            print(f"Notion day query failed {res.status_code}: {res.text[:200]}")
            return []
        for page in res.json().get("results", []):
            props = page.get("properties") or {}
            items.append(
                {
                    "time": "",
                    "ticker": _plain_from_prop(props.get("Ticker")),
                    "company": _plain_from_prop(props.get("Company")),
                    "title": _plain_from_prop(props.get("Title")),
                    "link": (props.get("Link") or {}).get("url") or "",
                    "ai_summary": _plain_from_prop(props.get("AI Summary")),
                }
            )
    except Exception as e:
        print(f"fetch_todays_rns_from_notion error: {e}")
    return items


def send_rns_of_the_day(items, date_label=None, chat_id=None):
    # type: (list, Optional[str], Optional[str]) -> int
    """
    Send the RNS of the Day one-pager to Telegram (NOTIFICATION_CHAT_ID).
    Also prints plain text to console for easy copy-paste.
    Returns number of messages sent.
    """
    messages = format_rns_of_the_day(items, date_label=date_label)
    # Console copy-paste (strip simple HTML tags for readability)
    print("\n" + "=" * 40)
    print("RNS OF THE DAY — copy below into Telegram")
    print("=" * 40)
    for m in messages:
        plain = re.sub(r"<[^>]+>", "", m)
        plain = plain.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        print(plain)
        print("-" * 40)
    sent = 0
    for m in messages:
        ok = send_telegram_msg(m, chat_id=chat_id)
        if ok:
            sent += 1
        time.sleep(0.5)
    print(f"RNS of the Day: {sent}/{len(messages)} message(s) sent to Telegram.")
    return sent


def run_daily_summary(date_iso=None):
    # type: (Optional[str]) -> None
    """Build + send one-pager from Notion Hive RNS News Log for a date."""
    items = fetch_todays_rns_from_notion(date_iso)
    print(f"Loaded {len(items)} RNS row(s) from Notion for {date_iso or 'today'}.")
    send_rns_of_the_day(items, date_label=date_iso)


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if "--summary" in args or "-s" in args:
        # Optional: python bot.py --summary 2026-09-16
        date_arg = None
        for a in args:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a):
                date_arg = a
                break
        run_daily_summary(date_arg)
    else:
        check_rns()
