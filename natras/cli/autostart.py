'''Autostart orchestrator. Run: python -m natras.cli.autostart

Triggered when the laptop connects to Wi-Fi (see scripts/register_autostart.ps1).
Flow: check window -> wait for internet -> if FYERS token is stale, send the login link
to Telegram and complete login -> run the data collector -> report to Telegram.

Login completion:
- http redirect URI  : local server catches the redirect (open the link on this laptop).
- https redirect URI : after login the browser shows a connection error page; copy the
  full address-bar URL and send it to the Telegram bot. Works from phone or laptop.

Data only. No orders. Secrets are never sent to Telegram or logged.
'''
import logging
import os
import re
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
from natras.cli._env import load_env
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


class Notifier:
    def __init__(self) -> None:
        self._token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        self._chat = os.environ.get('TELEGRAM_AUTHORIZED_USER_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')
        self.enabled = bool(self._token and self._chat) and 'your_' not in self._token and 'your_' not in self._chat

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

    def wait_for_message(self, match, timeout: int):
        '''Return the first text from the authorized chat that satisfies match(text).'''
        if not self.enabled:
            return None
        base = f'https://api.telegram.org/bot{self._token}/getUpdates'
        offset = None
        try:
            flush = requests.get(base, params={'offset': -1, 'timeout': 0}, timeout=15).json()
            items = flush.get('result', [])
            if items:
                offset = items[-1]['update_id'] + 1
        except (requests.RequestException, ValueError, KeyError):
            pass
        end = time.time() + timeout
        while time.time() < end:
            params = {'timeout': 20}
            if offset is not None:
                params['offset'] = offset
            try:
                data = requests.get(base, params=params, timeout=30).json()
            except (requests.RequestException, ValueError):
                time.sleep(3)
                continue
            for upd in data.get('result', []):
                offset = upd['update_id'] + 1
                msg = upd.get('message') or {}
                if str(msg.get('chat', {}).get('id')) != str(self._chat):
                    continue
                text = msg.get('text', '')
                if match(text):
                    return text
        return None


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
    print("Checking internet connectivity...", flush=True)
    end = time.time() + max_seconds
    while time.time() < end:
        try:
            requests.get('https://api.telegram.org', timeout=5)
            print("Internet connected.", flush=True)
            return True
        except requests.RequestException:
            time.sleep(5)
    print("Internet not reachable.", flush=True)
    return False


def _extract_url(text: str) -> str:
    found = re.search(r'https?://\S+', text)
    return found.group(0) if found else text.strip()


def ensure_fyers_token(notifier: Notifier) -> bool:
    creds = FyersCredentials.from_env()
    auth = FyersAuthenticator(creds)
    try:
        auth.get_access_token()
        print("FYERS token is valid.", flush=True)
        notifier.send('NAT RAS: FYERS token is valid. Starting data collection & paper engine.')
        return True
    except FyersAuthError:
        pass

    parsed = urlparse(creds.redirect_uri)
    url = auth.get_login_url()
    try:
        print(f"FYERS login needed. Opening login URL:\n{url}", flush=True)
        if parsed.scheme == 'http':
            notifier.send(
                'NAT RAS: FYERS login needed. Open this link ON THIS LAPTOP within 15 minutes:\n' + url
            )
            try:
                webbrowser.open(url)
            except Exception:
                pass
            redirected = wait_for_redirect(parsed.port or 5000, timeout=LOGIN_TIMEOUT_SECONDS)
        else:
            notifier.send(
                'NAT RAS: FYERS login needed. 1) Open this link and log in:\n' + url +
                '\n2) The browser will then show a connection error page. That is expected.\n'
                '3) Copy the FULL address-bar URL (it contains auth_code=) and send it here.'
            )
            try:
                webbrowser.open(url)
            except Exception:
                pass
            text = notifier.wait_for_message(lambda t: 'auth_code=' in t, LOGIN_TIMEOUT_SECONDS)
            if text is None:
                raise TimeoutError('No login URL received.')
            redirected = _extract_url(text)
        auth.exchange_auth_code(parse_auth_code(redirected))
    except TimeoutError:
        notifier.send('NAT RAS: FYERS login timed out. Collection NOT started.')
        return False
    except FyersAuthError as exc:
        notifier.send(f'NAT RAS: FYERS login failed: {exc}')
        return False
    notifier.send('NAT RAS: FYERS login successful. Starting data collection & paper engine.')
    return True


def run_collector(notifier: Notifier, force: bool = False) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    restarts = 0
    print("Launching collector, paper engine & market intelligence...", flush=True)
    while True:
        if not force and not in_window(datetime.now(IST)):
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
        n = db.execute(
            "SELECT COUNT(*) FROM candles WHERE ts LIKE ? AND source='fyers_live'", (today + '%',)
        ).fetchone()[0]
        db.close()
        notifier.send(f'NAT RAS: session finished. {n} live 5-minute candles saved today.')
    except Exception as exc:
        notifier.send(f'NAT RAS: collection finished; summary unavailable ({type(exc).__name__}).')


def main() -> int:
    load_env()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[
            logging.FileHandler(LOG_PATH, encoding='utf-8'),
            logging.StreamHandler(sys.stdout),
        ],
    )
    notifier = Notifier()
    force = '--force' in sys.argv
    now = datetime.now(IST)
    if not force and not in_window(now):
        msg = f"[{now:%H:%M:%S}] Outside weekday collection window (07:00 - 15:30 IST)."
        log.info(msg)
        print(msg)
        print("To launch immediately outside the trading window (e.g. testing), run with: --force")
        return 0
    if lock_active():
        msg = 'Another autostart instance is active; exiting.'
        log.info(msg)
        print(msg)
        return 0
    touch_lock()
    if not wait_for_internet():
        log.error('No internet; exiting.')
        return 1
    notifier.send('NAT RAS: laptop online. Checking FYERS session.')
    if not ensure_fyers_token(notifier):
        return 1
    run_collector(notifier, force=force)
    end_of_day_summary(notifier)
    try:
        LOCK_PATH.unlink()
    except OSError:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
