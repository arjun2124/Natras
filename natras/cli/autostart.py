'''Autostart orchestrator. Run: python -m natras.cli.autostart

Triggered when the laptop connects to Wi-Fi (see scripts/register_autostart.ps1).
Flow: check window -> wait for internet -> if FYERS token is stale, send the login link
to Telegram and catch the redirect -> run the data collector -> report to Telegram.
Data only. No orders. Secrets are never sent to Telegram or logged.
'''
import logging
import os
import sqlite3
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from natras.adapters.fyers_auth import (
    FyersAuthenticator,
    FyersAuthError,
    FyersCredentials,
    parse_auth_code,
)
from natras.cli.fyers_auto_login import wait_for_redirect
from natras.data.session import IST

LOG_PATH = Path('data') / 'logs' / 'autostart.log'
LOCK_PATH = Path('data') / 'autostart.lock'
DB_PATH = Path('data') / 'market.db'
WINDOW_START = (7, 0)
WINDOW_END = (15, 30)
LOGIN_TIMEOUT_SECONDS = 900
MAX_COLLECTOR_RESTARTS = 3
LOCK_FRESH_SECONDS = 90

log = logging.getLogger('natras.autostart')


def load_env(path: Path = Path('.env')) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Notifier:
    def __init__(self) -> None:
        self._token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        self._chat = os.environ.get('TELEGRAM_AUTHORIZED_USER_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')
        self.enabled = bool(self._token and self._chat) and 'your_' not in self._token

    def send(self, text: str) -> bool:
        log.info('TG: %s', text.splitlines()[0][:120])
        if not self.enabled:
            log.warning('Telegram not configured; message not sent.')
            return False
        try:
            r = requests.post(
                f'https://api.telegram.org/bot{self._token}/sendMessage',
                json={'chat_id': self._chat, 'text': text, 'disable_web_page_preview': True},
                timeout=15,
            )
            return r.status_code == 200
        except requests.RequestException as exc:
            log.warning('Telegram send failed: %s', type(exc).__name__)
            return False


def in_window(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return WINDOW_START <= (now.hour, now.minute) < WINDOW_END


def lock_active() -> bool:
    try:
        return time.time() - LOCK_PATH.stat().st_mtime < LOCK_FRESH_SECONDS
    except OSError:
        return False


def touch_lock() -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(str(os.getpid()))


def wait_for_internet(max_seconds: int = 180) -> bool:
    end = time.time() + max_seconds
    while time.time() < end:
        try:
            requests.get('https://api.telegram.org', timeout=5)
            return True
        except requests.RequestException:
            time.sleep(5)
    return False


def ensure_fyers_token(notifier: Notifier) -> bool:
    creds = FyersCredentials.from_env()
    auth = FyersAuthenticator(creds)
    try:
        auth.get_access_token()
        notifier.send('NAT RAS: FYERS token is valid. Starting data collection.')
        return True
    except FyersAuthError:
        pass

    port = urlparse(creds.redirect_uri).port or 5000
    url = auth.get_login_url()
    notifier.send(
        'NAT RAS: FYERS login needed. Open this link ON THIS LAPTOP within 15 minutes '
        '(the redirect returns to 127.0.0.1):\n' + url
    )
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        redirected = wait_for_redirect(port, timeout=LOGIN_TIMEOUT_SECONDS)
        auth.exchange_auth_code(parse_auth_code(redirected))
    except TimeoutError:
        notifier.send('NAT RAS: FYERS login timed out. Collection NOT started.')
        return False
    except FyersAuthError as exc:
        notifier.send(f'NAT RAS: FYERS login failed: {exc}')
        return False
    notifier.send('NAT RAS: FYERS login successful. Starting data collection.')
    return True


def run_collector(notifier: Notifier) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    restarts = 0
    while True:
        now = datetime.now(IST)
        if not in_window(now):
            break
        with open(LOG_PATH.parent / 'collector.log', 'a', encoding='utf-8') as out:
            proc = subprocess.Popen(
                [sys.executable, '-m', 'natras.cli.collect'], stdout=out, stderr=subprocess.STDOUT
            )
            while proc.poll() is None:
                touch_lock()
                time.sleep(30)
        if proc.returncode == 0:
            break
        restarts += 1
        notifier.send(f'NAT RAS: collector exited with code {proc.returncode} (restart {restarts}).')
        if restarts > MAX_COLLECTOR_RESTARTS:
            notifier.send('NAT RAS: too many failures. Collection stopped. Check data/logs/collector.log.')
            return
        time.sleep(15 * restarts)


def end_of_day_summary(notifier: Notifier) -> None:
    try:
        db = sqlite3.connect(DB_PATH)
        today = datetime.now(IST).date().isoformat()
        n = db.execute("SELECT COUNT(*) FROM candles WHERE ts LIKE ? AND source='fyers_live'", (today + '%',)).fetchone()[0]
        db.close()
        notifier.send(f'NAT RAS: collection finished. {n} live 5-minute candles saved today.')
    except Exception as exc:
        notifier.send(f'NAT RAS: collection finished; summary unavailable ({type(exc).__name__}).')


def main() -> int:
    load_env()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'
    )
    notifier = Notifier()
    now = datetime.now(IST)
    if not in_window(now):
        log.info('Outside weekday collection window; exiting.')
        return 0
    if lock_active():
        log.info('Another autostart instance is active; exiting.')
        return 0
    touch_lock()
    if not wait_for_internet():
        log.error('No internet; exiting.')
        return 1
    notifier.send('NAT RAS: laptop online. Checking FYERS session.')
    if not ensure_fyers_token(notifier):
        return 1
    run_collector(notifier)
    end_of_day_summary(notifier)
    try:
        LOCK_PATH.unlink()
    except OSError:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
