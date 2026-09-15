"""
One-off / on-demand backfill:
  For every ticker in UK AIM Micro-Cap Notion DB, pull last N RNS from Investegate,
  extract Summary by AI (#collapseSummary), write rows to RNS News Log,
  and refresh Last 3 RNS + Last RNS Date on the ticker page.

Env (same as bot):
  NOTION_TOKEN, NOTION_TICKERS_DB_ID, NOTION_RNS_DB_ID
Optional:
  BACKFILL_LIMIT_TICKERS=50   # process only first N tickers (testing)
  BACKFILL_PER_TICKER=3       # default 3
  BACKFILL_OFFSET=0           # skip first N tickers (resume)
"""
import os
import re
import time
import hashlib
import requests
from html import unescape
from datetime import datetime
from urllib.parse import urljoin

from bot import (
    load_tickers_from_notion,
    find_notion_page_id,
    update_last_rns_date,
    update_last_3_rns,
    create_rns_log_entry,
    extract_ai_summary,
    _normalize_uuid,
    NOTION_TOKEN,
    NOTION_HEADERS,
    NOTION_RNS_DB_ID,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.5 Safari/605.1.15"
    )
}
PER_TICKER = int(os.getenv("BACKFILL_PER_TICKER", "3"))
LIMIT = os.getenv("BACKFILL_LIMIT_TICKERS")
OFFSET = int(os.getenv("BACKFILL_OFFSET", "0"))


def get_company_anns(ticker, limit=3):
    url = f"https://www.investegate.co.uk/company/{ticker}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"  company page {ticker}: HTTP {r.status_code}")
            return []
        hrefs = re.findall(
            r'href=["\'](https://www\.investegate\.co\.uk/announcement/rns/[^"\']+)["\']',
            r.text,
            flags=re.I,
        )
        seen = set()
        links = []
        for h in hrefs:
            h = h.split("?")[0]
            if h in seen:
                continue
            seen.add(h)
            m = re.search(r"/announcement/rns/[^/]+/([^/]+)/(\d+)", h)
            title = m.group(1).replace("-", " ").strip().title() if m else "RNS"
            links.append({"url": h, "title": title[:200]})
            if len(links) >= limit:
                break
        return links
    except Exception as e:
        print(f"  list error {ticker}: {e}")
        return []


def parse_date_from_page(html):
    dm = re.search(
        r"(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})",
        html or "",
    )
    if not dm:
        return None
    date_str = dm.group(1)
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return None


def already_logged(rns_hash):
    """Skip if RNS Hash already exists in RNS News Log."""
    if not NOTION_TOKEN or not NOTION_RNS_DB_ID:
        return False
    db_id = _normalize_uuid(NOTION_RNS_DB_ID)
    try:
        res = requests.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=NOTION_HEADERS,
            json={
                "page_size": 1,
                "filter": {"property": "RNS Hash", "rich_text": {"equals": rns_hash}},
            },
            timeout=15,
        )
        if res.status_code == 200:
            return len(res.json().get("results", [])) > 0
    except Exception as e:
        print(f"  dedupe check error: {e}")
    return False


def main():
    tickers = load_tickers_from_notion()
    if not tickers:
        print("No tickers from Notion.")
        return

    tickers = tickers[OFFSET:]
    if LIMIT:
        tickers = tickers[: int(LIMIT)]

    print(f"Backfill starting: {len(tickers)} tickers, {PER_TICKER} RNS each (offset={OFFSET})")
    written = 0
    skipped = 0

    for i, ticker in enumerate(tickers):
        print(f"[{i+1}/{len(tickers)}] {ticker}")
        anns = get_company_anns(ticker, PER_TICKER)
        page_id = find_notion_page_id(ticker)
        last3_blocks = []
        latest_date = None

        for a in anns:
            rns_hash = hashlib.md5(f"{ticker}_{a['url']}".encode()).hexdigest()
            if already_logged(rns_hash):
                print(f"  skip existing {a['title'][:40]}")
                skipped += 1
                continue

            # extract_ai_summary already uses #collapseSummary
            ai = extract_ai_summary(a["url"])
            # date from page
            try:
                html = requests.get(a["url"], headers=HEADERS, timeout=15).text
                iso = parse_date_from_page(html)
            except Exception:
                iso = None
            if not iso:
                iso = datetime.utcnow().strftime("%Y-%m-%d")

            ok = create_rns_log_entry(
                ticker=ticker,
                company=ticker,
                title=a["title"],
                rns_date_iso=iso,
                link=a["url"],
                ai_summary=ai or "",
                rns_hash=rns_hash,
            )
            if ok:
                written += 1
                print(f"  + {iso} | {a['title'][:50]} | summary={bool(ai)}")
            else:
                print(f"  ! failed write {a['title'][:40]}")

            snippet = (ai or a["title"]).strip()
            if len(snippet) > 280:
                snippet = snippet[:277] + "..."
            last3_blocks.append(f"• {iso} | {a['title']}\n  {snippet}")
            if latest_date is None or iso > latest_date:
                latest_date = iso

            time.sleep(0.25)

        if page_id and last3_blocks:
            # write Last 3 in chronological order newest first (anns are already newest first)
            if latest_date:
                update_last_rns_date(page_id, latest_date)
            # use first block as "latest" via update_last_3_rns helper repeatedly
            # simpler: direct patch built text
            new_text = "\n\n".join(last3_blocks[:3])
            if len(new_text) > 1900:
                new_text = new_text[:1897] + "..."
            try:
                requests.patch(
                    f"https://api.notion.com/v1/pages/{page_id}",
                    headers=NOTION_HEADERS,
                    json={
                        "properties": {
                            "Last 3 RNS": {
                                "rich_text": [{"text": {"content": new_text}}]
                            }
                        }
                    },
                    timeout=15,
                )
                print(f"  → Last 3 RNS updated")
            except Exception as e:
                print(f"  → Last 3 RNS error: {e}")

        time.sleep(0.3)

    print(f"Done. written={written} skipped={skipped}")


if __name__ == "__main__":
    main()
