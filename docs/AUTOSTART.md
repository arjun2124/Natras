# NAT RAS autostart (data collection only)

## What it does
When the laptop connects to a network (or you log in) on a weekday between 07:00 and 15:30 IST:
1. Waits for internet and sends a Telegram message.
2. If the FYERS token is stale, sends the FYERS login link to Telegram and also opens it in the browser.
3. Catches the redirect on `127.0.0.1` (port from `FYERS_REDIRECT_URI`), saves the token locally, validates it.
4. Runs `natras.cli.collect` (5-minute candles to `data/market.db`), restarting up to 3 times on failure.
5. Sends an end-of-day summary.

No orders are placed. `LIVE_ORDERS_ENABLED` stays false.

## Limits
- FYERS login needs you to log in each day. It cannot be fully silent.
- The login link must be opened on the same laptop, because the redirect goes to 127.0.0.1.
- Telegram messages contain the login URL but never tokens or secrets.

## Setup
1. `pip install fyers-apiv3 requests`
2. In `.env` set: `FYERS_APP_ID`, `FYERS_SECRET_KEY`, `FYERS_REDIRECT_URI=http://127.0.0.1:5000/`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_AUTHORIZED_USER_ID` (your numeric chat id; message the bot once first).
3. Test: `python -m natras.cli.autostart`
4. Register: `powershell -ExecutionPolicy Bypass -File scripts/register_autostart.ps1`
5. Test the task: `Start-ScheduledTask -TaskName NatRasAutoStart`

Logs: `data/logs/autostart.log`, `data/logs/collector.log`.
