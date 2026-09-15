# R_News on Railway – setup checklist

Primary runner is **Railway** (`python worker.py`).  
GitHub Actions is **manual backup only** (do not enable a schedule while Railway is live).

## Live auto-sync (what happens on each new RNS)

Every scan cycle (`check_rns`) for a **new** release:

1. **Telegram** notification channel + watchlist DMs  
2. **Notion RNS News Log** – new row (title, ticker, date, link, AI summary)  
3. **UK AIM Micro-Cap** – `Last RNS Date` + rolling `Last 3 RNS`  

Scan cadence (London time):

| Window | Interval |
|--------|----------|
| Mon–Fri 07:00–16:30 | **1 minute** (`SCAN_INTERVAL_MARKET_MIN`) |
| Other times | **3 minutes** (`SCAN_INTERVAL_MINUTES`) |

## 1. Deploy from GitHub

1. Open [Railway](https://railway.app) → **New Project**
2. **Deploy from GitHub repo** → `rpug26/R_News`
3. Wait for the first deploy (may fail until variables are set – that is OK)

## 2. Variables

Project → service → **Variables** → add:

```
TELEGRAM_TOKEN=...
NOTIFICATION_CHAT_ID=...
LOG_CHAT_ID=...
COMMAND_CHAT_ID=...
NOTION_TOKEN=...
NOTION_TICKERS_DB_ID=021838c4-6624-4a1e-b4d0-26d37e29095a
NOTION_RNS_DB_ID=a79316999ab94fe68a8174d86146ae1a
NOTION_WATCHLIST_DB_ID=...
SCAN_INTERVAL_MINUTES=3
SCAN_INTERVAL_MARKET_MIN=1
STATE_DIR=/data
```

Optional: `GH_PAT` only if you still use `/ADD`/`/REMOVE` against `tickers.txt` on GitHub.

Do **not** commit these values to the repo.

## 3. Notion sharing (required for auto-sync)

Share both databases with integration **Hive Stock Picks**:

- UK AIM Micro-Cap  
- RNS News Log  

Without this, Telegram may still fire but Notion writes return 404.

## 4. Volume (required to avoid re-alerts)

Without a volume, `last_rns_ids.txt` is lost on every redeploy and old RNS items can fire again.

1. Railway service → **Settings** (or **Volumes**)
2. **Add Volume**
3. Mount path: `/data`
4. Confirm variable `STATE_DIR=/data`

Worker writes: `/data/last_rns_ids.txt`

## 5. Start command

Should already be set via `railway.toml`:

```
python worker.py
```

Redeploy after adding variables + volume.

## 6. Verify

In **Deploy Logs** look for:

```
R_News worker starting (market=1m, off=3m, state_dir=/data)
Cycle start
Loaded N tickers from Notion.
  → Notion Last RNS Date updated for TICKER
  → Notion Last 3 RNS updated for TICKER
Cycle done
```

Then confirm:
- Notification channel receives new RNS alerts (with AI summary)
- Log channel receives scan summaries
- **RNS News Log** gains a new row within ~1 minute of the wire
- **Last RNS Date** / **Last 3 RNS** update on the ticker page

## 7. GitHub Actions

- Schedule stays **off**
- Use **Run workflow** only for emergency one-off scans when Railway is down
- Never run Actions on a timer while Railway is scanning

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Crash / exit | Variables missing (`TELEGRAM_TOKEN`) |
| No tickers | `NOTION_TOKEN` + `NOTION_TICKERS_DB_ID` + integration shared |
| Telegram works, Notion empty | `NOTION_RNS_DB_ID` missing or RNS News Log not shared with integration |
| Duplicate alerts after redeploy | Volume not mounted / `STATE_DIR` wrong |
| Double posts | Railway + Actions both running |
| Scrape errors | Investegate blocking; check logs for status codes |
