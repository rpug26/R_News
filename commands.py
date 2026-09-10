import requests
import os
import base64

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
GITHUB_TOKEN = os.getenv("GH_PAT")
LOG_CHAT_ID = os.getenv("LOG_CHAT_ID")
REPO_NAME = "rpug26/R_News"
TICKER_FILE = "tickers.txt"

# Pull secret and split by comma, cleaning up any accidental spaces
AUTHORIZED_IDS = [id.strip() for id in os.getenv("COMMAND_CHAT_ID", "").split(",") if id.strip()]

def log_command(user, action, details):
    """Sends a log message to the dedicated Telegram channel."""
    if not LOG_CHAT_ID: return
    msg = f"🛠 <b>Command Log:</b>\nUser: {user}\nAction: {action}\nDetails: {details}"
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": LOG_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"Failed to log command: {e}")

def broadcast_msg(text):
    """Sends a message to EVERYONE in the AUTHORIZED_IDS list."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    for chat_id in AUTHORIZED_IDS:
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        try:
            res = requests.post(url, params=params, timeout=10)
            if res.status_code != 200:
                print(f"Error sending to {chat_id}: {res.text}")
        except Exception as e:
            print(f"Connection error for {chat_id}: {e}")

def update_github_file(new_content_str, message):
    file_url = f"https://api.github.com/repos/{REPO_NAME}/contents/{TICKER_FILE}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    
    # Get the current file to get the SHA (required for GitHub API updates)
    res = requests.get(file_url, headers=headers)
    sha = res.json().get('sha') if res.status_code == 200 else None

    payload = {
        "message": message,
        "content": base64.b64encode(new_content_str.encode('utf-8')).decode('utf-8'),
        "sha": sha
    }
    
    put_res = requests.put(file_url, headers=headers, json=payload)
    return put_res.status_code

def handle_commands():
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN not found.")
        return

    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    try:
        response = requests.get(url, params={"limit": 10, "timeout": 1}).json()
        updates = response.get("result", [])
        
        last_id = 0
        for update in updates:
            last_id = update.get("update_id")
            msg_data = update.get("message") or update.get("channel_post")
            if not msg_data: continue
            
            # Security Check
            current_chat_id = str(msg_data.get("chat", {}).get("id"))
            sender_name = msg_data.get("from", {}).get("first_name", "User")
            
            if current_chat_id not in AUTHORIZED_IDS:
                print(f"Ignored unauthorized ID: {current_chat_id}")
                continue

            text = msg_data.get("text", "").strip()
            
            # Load current tickers
            if os.path.exists(TICKER_FILE):
                with open(TICKER_FILE, "r") as f:
                    current_tickers = [t.strip().upper() for t in f.read().splitlines() if t.strip()]
            else:
                current_tickers = []

            # Command: /ADD
            if text.upper().startswith("/ADD "):
                raw_input = text[5:].strip().upper()
                new_tickers = [t.strip() for t in raw_input.split(",") if t.strip()]
                added = [t for t in new_tickers if t not in current_tickers]
                
                if added:
                    current_tickers.extend(added)
                    status = update_github_file("\n".join(current_tickers), f"Add {added} by {sender_name}")
                    if status in [200, 201]:
                        broadcast_msg(f"✅ <b>{sender_name}</b> added: <b>{', '.join(added)}</b>")
                        log_command(sender_name, "ADD TICKER", f"{', '.join(added)}")
                else:
                    broadcast_msg(f"ℹ️ {sender_name} tried adding tickers already in list.")
                    log_command(sender_name, "ADD TICKER FAILED", f"Tickers already in list: {raw_input}")

            # Command: /REMOVE
            elif text.upper().startswith("/REMOVE "):
                ticker = text[8:].strip().upper()
                if ticker in current_tickers:
                    current_tickers.remove(ticker)
                    status = update_github_file("\n".join(current_tickers), f"Remove {ticker} by {sender_name}")
                    if status in [200, 201]:
                        broadcast_msg(f"✅ <b>{sender_name}</b> removed: <b>{ticker}</b>")
                        log_command(sender_name, "REMOVE TICKER", ticker)
                else:
                    broadcast_msg(f"ℹ️ {sender_name} tried removing <b>{ticker}</b> (not found).")
                    log_command(sender_name, "REMOVE TICKER FAILED", f"Ticker not found: {ticker}")

            # Command: /LIST
            elif text.upper() == "/LIST":
                if current_tickers:
                    msg = f"📋 <b>Watchlist (Requested by {sender_name}):</b>\n\n" + "\n".join([f"• {t}" for t in sorted(current_tickers)])
                else:
                    msg = f"📋 Watchlist is empty (Requested by {sender_name})."
                broadcast_msg(msg)
                log_command(sender_name, "LIST TICKERS", "Successfully requested list.")

        # Clear updates so they don't repeat next run
        if last_id > 0:
            requests.get(url, params={"offset": last_id + 1})

    except Exception as e:
        print(f"Command Error: {e}")
        log_command("System", "ERROR", str(e))

if __name__ == "__main__":
    handle_commands()
