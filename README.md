# R_News – UK Micro-Cap RNS Alert Bot

Monitors Investegate for UK micro-cap RNS announcements and posts alerts to Telegram.

## Setup (your own accounts only)

1. Create a new Telegram bot via @BotFather → get `TELEGRAM_TOKEN`
2. Create three Telegram chat IDs:
   - Notification channel/group
   - Log channel/group
   - Your personal chat ID (for commands)
3. Create a GitHub PAT with `repo` scope → `GH_PAT`
4. Add these secrets in this repo (Settings → Secrets and variables → Actions):
   - `TELEGRAM_TOKEN`
   - `NOTIFICATION_CHAT_ID`
   - `LOG_CHAT_ID`
   - `COMMAND_CHAT_ID`
   - `GH_PAT`

## Commands (from authorised chat)
- `/ADD TICKER1,TICKER2`
- `/REMOVE TICKER`
- `/LIST`

## Running
Use the GitHub Actions workflow “RNS Alert Bot” (manual trigger or repository_dispatch).
