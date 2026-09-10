import requests
from curl_cffi import requests as c_requests # NEW: The stealth scraper
from bs4 import BeautifulSoup
import os
import hashlib
import re
from urllib.parse import urljoin
import time
import json

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTIFICATION_CHAT_ID = os.getenv("NOTIFICATION_CHAT_ID")
LOG_CHAT_ID = os.getenv("LOG_CHAT_ID")
FILE_NAME = "last_rns_ids.txt"
TICKER_FILE = "tickers.txt"

def log_to_telegram(message):
    """Prints to console and sends a log to the dedicated Telegram channel."""
    print(message) # Still print to GitHub logs
    if not LOG_CHAT_ID:
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

def load_tickers():
    if os.path.exists(TICKER_FILE):
        with open(TICKER_FILE, "r") as f:
            lines = f.read().splitlines()
            return [line.strip().upper() for line in lines if line.strip()]
    return []

def send_telegram_msg(text, rns_url=None, max_retries=3):
    """Sends a message to Telegram, with smart retry logic for 429 Rate Limits."""
    if not NOTIFICATION_CHAT_ID:
        print("Error: NOTIFICATION_CHAT_ID not set.")
        return
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    # payload sent as JSON ensures nested link_preview_options are parsed correctly
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
                return  # Success! Exit the function.
                
            elif res.status_code == 429:
                # Telegram is telling us to slow down (too many messages)
                error_data = res.json()
                retry_after = error_data.get("parameters", {}).get("retry_after", 30)
                # Log this to your admin channel so you know it's happening
                log_to_telegram(f"⚠️ Rate limited by Telegram! Pausing for {retry_after} seconds...")
                time.sleep(retry_after) # Wait exactly as long as Telegram asked
                
            else:
                print(f"Telegram API Error: {res.text}")
                break # Don't retry on other types of errors (like bad formatting)
                
        except Exception as e:
            print(f"Telegram connection error: {e}")
            time.sleep(5) # Wait 5 seconds on general network errors before trying again
            
    print("Failed to send message after maximum retries.")

def check_rns():
    tickers = load_tickers()
    if not tickers:
        log_to_telegram("Watchlist is empty. No tickers to scan.")
        return
    print(f"Starting scan for {len(tickers)} tickers.")

    base_url = "https://www.investegate.co.uk"
    today_url = urljoin(base_url, "/today-announcements/?perPage=300")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    last_seen_hashes = set()
    if os.path.exists(FILE_NAME):
        with open(FILE_NAME, "r") as f:
            for line in f:
                parts = line.strip().split(" | ")
                if parts:
                    last_seen_hashes.add(parts[-1])

    try:
        # --- NEW: Scraper Retry Loop ---
        max_scrape_retries = 3
        table = None
        response_status = None
        
        for attempt in range(max_scrape_retries):
            try:
                response = c_requests.get(today_url, impersonate="safari15_5", timeout=15)
                response_status = response.status_code
                
                if response_status == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    table = soup.find('table')
                    if table:
                        break # Success! We found the table, exit the retry loop
                
                # If we get here, it means we either didn't get a 200 OK, or we didn't find the table
                print(f"⚠️ Scrape attempt {attempt + 1} failed (Status: {response_status}). Retrying in 5 seconds...")
                time.sleep(5)
                
            except Exception as e:
                print(f"⚠️ Network error on attempt {attempt + 1}: {e}. Retrying in 5 seconds...")
                time.sleep(5)

        # After 3 tries, if we still don't have a table, we have to give up for this run
        if not table:
            print(f"❌ Could not find the announcements table after {max_scrape_retries} attempts. Last HTTP Status: {response_status}")
            return
        # -------------------------------
        
        rows = table.find_all('tr')
        news_found = 0
        
        # --- NEW: List to hold batched log updates ---
        batched_log_entries = []

        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 4:
                continue
            
            rns_time = cols[0].get_text().strip()
            company_raw = cols[2].get_text().upper()
            announcement_cell = cols[3]
            
            for ticker in tickers:
                if re.search(rf'\({re.escape(ticker)}\)', company_raw):
                    link_tag = announcement_cell.find('a', href=True)
                    if not link_tag:
                        continue
                        
                    title = link_tag.get_text().strip()
                    # Clean the link of trailing question marks
                    full_link = urljoin(base_url, link_tag['href']).strip().rstrip('?')
                    
                    unique_string = f"{rns_time}_{ticker}_{title}_{full_link}"
                    rns_id = hashlib.md5(unique_string.encode()).hexdigest()

                    if rns_id not in last_seen_hashes:
                        clean_company = company_raw.split('(')[0].replace('\n', ' ').strip()
                        clean_company = re.sub(' +', ' ', clean_company)
                        
                        # Print to GitHub Actions console for debugging
                        print(f"[{rns_time}] MATCH: {ticker} | Hash: {rns_id[:12]}")
                        
                        # --- NEW: Append to our batched log list instead of sending immediately ---
                        batched_log_entries.append(f"• [{rns_time}] <b>{ticker}</b> - {clean_company}")
                        
                        # --- This still sends the full alert to your main notification channel immediately ---
                        msg = (f"🕒 <b>{rns_time}</b>\n"
                               f"📰 <b>#{ticker} - {clean_company}</b>\n"
                               f"{title}\n\n"
                               f"🔗 <a href='{full_link}'>Read Full Release</a>")
                        
                        # --- CACHE BUSTER ---
                        preview_url = f"{full_link}?t={int(time.time())}"
                        
                        send_telegram_msg(msg, rns_url=preview_url)
                        
                        time.sleep(1) # Standard anti-flood delay (smart retry handles the big limits)
                        
                        log_entry = f"{rns_time} | {ticker} | {rns_id}"
                        with open(FILE_NAME, "a") as f:
                            f.write(log_entry + "\n")
                        
                        last_seen_hashes.add(rns_id)
                        news_found += 1
                    
                    break # Move to next table row once match is found
        
        # --- NEW: Send the batched log summary ---
        if news_found > 0:
            summary_msg = f"Found {news_found} new items:\n\n" + "\n".join(batched_log_entries)
            
            # Safeguard against Telegram's 4096 character limit per message
            if len(summary_msg) > 4000:
                summary_msg = summary_msg[:4000] + "\n\n<i>... [Log truncated due to length]</i>"
                
            log_to_telegram(summary_msg)
        else:
            print("Scan complete. No new items.")
            
    except Exception as e:
        log_to_telegram(f"Scraper Error: {e}")

if __name__ == "__main__":
    check_rns()
