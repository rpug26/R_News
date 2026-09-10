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
