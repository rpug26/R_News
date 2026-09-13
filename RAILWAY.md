# R_News on Railway – setup checklist

Primary runner is **Railway** (`python worker.py`).  
GitHub Actions is **manual backup only** (do not enable a schedule while Railway is live).

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
NOTION_TICKERS_DB_ID=...
SCAN_INTERVAL_MINUTES=15
STATE_DIR=/data
```

Optional: `GH_PAT` only if you still use `/ADD`/`/REMOVE` against `tickers.txt` on GitHub.

Do **not** commit these values to the repo.

## 3. Volume (required to avoid re-alerts)

Without a volume, `last_rns_ids.txt` is lost on every redeploy and old RNS items can fire again.

1. Railway service → **Settings** (or **Volumes**)
2. **Add Volume**
3. Mount path: `/data`
4. Confirm variable `STATE_DIR=/data`

Worker writes: `/data/last_rns_ids.txt`

## 4. Start command

Should already be set via `railway.toml`:

```
python worker.py
```

Redeploy after adding variables + volume.

## 5. Verify

In **Deploy Logs** look for:

```
R_News worker starting (interval=15m, state_dir=/data)
Cycle start
Loaded N tickers from Notion.
Cycle done
```

Then confirm:
- Notification channel receives new RNS alerts
- Log channel receives scan summaries
- Notion **Last RNS Date** updates on matched tickers

## 6. GitHub Actions

- Schedule stays **off**
- Use **Run workflow** only for emergency one-off scans when Railway is down
- Never run Actions on a timer while Railway is scanning

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Crash / exit | Variables missing (`TELEGRAM_TOKEN`) |
| No tickers | `NOTION_TOKEN` + `NOTION_TICKERS_DB_ID` + integration shared with UK AIM Micro-Cap |
| Duplicate alerts after redeploy | Volume not mounted / `STATE_DIR` wrong |
| Double posts | Railway + Actions both running |
| Scrape errors | Investegate blocking; check logs for status codes |
