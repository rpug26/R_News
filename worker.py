#!/usr/bin/env python3
"""
R_News Railway worker – runs command handler + RNS scan on a loop.

Env:
  SCAN_INTERVAL_MINUTES  default 15
  STATE_DIR              directory for last_rns_ids.txt (use a Railway volume path if set)
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_DIR = os.getenv("STATE_DIR", ".").strip() or "."
os.makedirs(STATE_DIR, exist_ok=True)

# Point bot.py at a durable path when a volume is mounted
os.environ.setdefault(
    "RNS_STATE_FILE",
    os.path.join(STATE_DIR, "last_rns_ids.txt"),
)

INTERVAL_MIN = max(1, int(os.getenv("SCAN_INTERVAL_MINUTES", "15")))


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


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

    print(f"[{_ts()}] Cycle done – sleeping {INTERVAL_MIN} minute(s)")


def main() -> None:
    required = ["TELEGRAM_TOKEN"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"Missing required env: {', '.join(missing)}")
        sys.exit(1)

    print(
        f"[{_ts()}] R_News worker starting "
        f"(interval={INTERVAL_MIN}m, state_dir={STATE_DIR})"
    )
    while True:
        run_once()
        time.sleep(INTERVAL_MIN * 60)


if __name__ == "__main__":
    main()
