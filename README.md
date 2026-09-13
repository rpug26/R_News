# R_News – UK Micro-Cap RNS Alert Bot

Monitors Investegate for UK micro-cap RNS announcements, posts alerts to Telegram, and writes **Last RNS Date** back into your Notion UK AIM Micro-Cap database.

## Architecture (v2)

- **Live ticker source**: Notion `UK AIM Micro-Cap` database (falls back to `tickers.txt`)
- **Write-back**: On every new RNS the bot updates the matching page’s `Last RNS Date` property
- **Telegram**: Notification + Log channels
- **Commands** (still available): `/ADD`, `/REMOVE`, `/LIST` (file-based fallback)

## Required Secrets (GitHub Actions)

| Secret | Description |
|--------|-------------|
| `TELEGRAM_TOKEN` | New bot from @BotFather |
| `NOTIFICATION_CHAT_ID` | Channel/group for RNS alerts |
| `LOG_CHAT_ID` | Channel for bot logs |
| `COMMAND_CHAT_ID` | Your Telegram user ID(s) for commands |
| `GH_PAT` | GitHub PAT with repo scope (for tickers.txt updates) |
| `NOTION_TOKEN` | Notion integration token (same as Hive-bot) |
| `NOTION_TICKERS_DB_ID` | `021838c4-6624-4a1e-b4d0-26d37e29095a` (UK AIM Micro-Cap) |

## Next upgrades planned
1. Snapshot / thesis commands (`#TICKER snapshot`)
2. Nightly alpha screening digest
3. Shared `hive_core` package with the main Hive-bot

## Deploy on Railway

R_News is a **scheduled scanner** (not a always-on chat bot like hive-bot). On Railway it runs as a **worker** that loops:

1. `commands.py` – process `/ADD`, `/REMOVE`, `/LIST`
2. `bot.py` – scrape Investegate + Telegram + Notion Last RNS Date
3. Sleep `SCAN_INTERVAL_MINUTES` (default 15)

### 1. Create project
1. [Railway](https://railway.app) → **New Project** → **Deploy from GitHub**
2. Select repo `rpug26/R_News`
3. Railway will detect `railway.toml` / `Dockerfile`

### 2. Set Variables (do **not** put these in git)

| Variable | Required | Notes |
|----------|----------|--------|
| `TELEGRAM_TOKEN` | Yes | BotFather token |
| `NOTIFICATION_CHAT_ID` | Yes | Channel/group for RNS alerts |
| `LOG_CHAT_ID` | Recommended | Log channel |
| `COMMAND_CHAT_ID` | For `/ADD` etc. | Comma-separated Telegram user IDs |
| `NOTION_TOKEN` | Yes (for live tickers) | Same integration as Hive-bot |
| `NOTION_TICKERS_DB_ID` | Yes | UK AIM Micro-Cap database ID |
| `GH_PAT` | Optional | Only if using file-based ticker commands on GitHub |
| `SCAN_INTERVAL_MINUTES` | Optional | Default `15` |
| `STATE_DIR` | Recommended | e.g. `/data` with a Volume |

### 3. Persist `last_rns_ids.txt` (important)
Railway’s disk is **ephemeral** unless you attach a **Volume**.

Without a volume, a redeploy can re-alert old RNS items.

**Recommended:** add a Volume mounted at `/data`, and set:

```
STATE_DIR=/data
```

The worker stores `last_rns_ids.txt` under `STATE_DIR`.

### 4. Start command
Default (from `railway.toml`):

```
python worker.py
```

### 5. After deploy
- Check **Deploy Logs** for `R_News worker starting`
- You should see scan cycles every N minutes
- Confirm Telegram notification + log channels receive messages

### GitHub Actions vs Railway
You can keep the GitHub Actions workflow for manual runs, or disable the schedule and rely on Railway only. Prefer **one** primary runner so you do not double-post alerts.
