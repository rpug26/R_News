#!/usr/bin/env python3
"""
R_News Railway worker – command handler + RNS scan loop + daily digest.

Env:
  SCAN_INTERVAL_MINUTES / SCAN_INTERVAL_MARKET_MIN
  STATE_DIR
  DIGEST_ENABLED (default 1)
  DIGEST_HOUR / DIGEST_MINUTE (default 16 / 45 London)
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_DIR = os.getenv("STATE_DIR", ".").strip() or "."
os.makedirs(STATE_DIR, exist_ok=True)

os.environ.setdefault(
    "RNS_STATE_FILE",
    os.path.join(STATE_DIR, "last_rns_ids.txt"),
)
os.environ.setdefault(
    "DIGEST_STATE_FILE",
    os.path.join(STATE_DIR, "last_digest_date.txt"),
)

INTERVAL_OFF = max(1, int(os.getenv("SCAN_INTERVAL_MINUTES", "3")))
INTERVAL_MKT = max(1, int(os.getenv("SCAN_INTERVAL_MARKET_MIN", "1")))


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _london_now():
    if ZoneInfo is not None:
        return datetime.now(ZoneInfo("Europe/London"))
    return datetime.now(timezone.utc)


def _is_uk_market_hours() -> bool:
    now = _london_now()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return (7 * 60) <= minutes <= (16 * 60 + 30)


def _next_interval_min() -> int:
    return INTERVAL_MKT if _is_uk_market_hours() else INTERVAL_OFF


def _env_check() -> None:
    required = ["TELEGRAM_TOKEN"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"Missing required env: {', '.join(missing)}")
        sys.exit(1)
    for key in (
        "NOTION_TOKEN",
        "NOTION_TICKERS_DB_ID",
        "NOTION_RNS_DB_ID",
        "NOTIFICATION_CHAT_ID",
    ):
        if not os.getenv(key):
            print(f"[{_ts()}] WARNING: {key} not set – features may be incomplete")


def run_once() -> None:
    print(f"[{_ts()}] Cycle start")
    try:
        from commands import handle_commands

        handle_commands()
    except Exception as e:
        print(f"[{_ts()}] commands.py error: {e}")
        traceback.print_exc()

    try:
        from bot import check_rns

        check_rns()
    except Exception as e:
        print(f"[{_ts()}] bot.py error: {e}")
        traceback.print_exc()

    try:
        from daily_digest import send_daily_digest

        if send_daily_digest(force=False):
            print(f"[{_ts()}] Daily digest posted")
    except Exception as e:
        print(f"[{_ts()}] daily digest error: {e}")
        traceback.print_exc()

    wait = _next_interval_min()
    print(f"[{_ts()}] Cycle done – next scan in {wait} minute(s)")


def main() -> None:
    _env_check()
    print(
        f"[{_ts()}] R_News worker starting "
        f"(market={INTERVAL_MKT}m, off={INTERVAL_OFF}m, state_dir={STATE_DIR})"
    )
    while True:
        run_once()
        time.sleep(_next_interval_min() * 60)


if __name__ == "__main__":
    main()
