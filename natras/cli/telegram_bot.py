"""Telegram bot polling CLI for NAT RAS.

Run:
    python -m natras.cli.telegram_bot --listen
    python -m natras.cli.telegram_bot --send "Test message"
    python -m natras.cli.telegram_bot --status
"""

from __future__ import annotations

import argparse
import sys
import time

from natras.adapters.telegram_live import TelegramLiveBot
from natras.cli._env import load_env


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="NAT RAS Telegram Bot Runner")
    parser.add_argument("--listen", action="store_true", help="Start long polling for user commands")
    parser.add_argument("--send", type=str, help="Send a notification message to the authorized chat")
    parser.add_argument("--status", action="store_true", help="Print bot connectivity and status")
    args = parser.parse_args()

    bot = TelegramLiveBot()
    if not bot.enabled:
        print("Telegram bot not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_AUTHORIZED_USER_ID in .env.")
        return 1

    if args.send:
        ok = bot.send_message(args.send)
        print(f"Message send result: {'SUCCESS' if ok else 'FAILED'}")
        return 0 if ok else 1

    if args.status:
        print("Telegram bot is configured and ready.")
        print("Authorized Chat ID:", bot.chat_id)
        return 0

    # Default or --listen: start polling loop
    print("NAT RAS Telegram Bot listening for commands (/status, /open_positions, /today_report, etc.)...")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            bot.poll_once()
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nTelegram bot polling stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
