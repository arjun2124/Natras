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
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

from natras.adapters.fyers_auth import (
    FyersAuthenticator,
    FyersAuthError,
    FyersCredentials,
    parse_auth_code,
)
from natras.cli._env import load_env
from natras.data.session import IST
from natras.resilience.auto_healer import AutoHealer

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

    def wait_for_message(self, match, timeout: int, stop_event: Optional[threading.Event] = None):
        '''Return the first text from the authorized chat that satisfies match(text).'''
        if not self.enabled:
            return None
        base = f'https://api.telegram.org/bot{self._token}/getUpdates'
        offset = None
        try:
            flush = requests.get(base, params={'offset': -5, 'timeout': 0}, timeout=10).json()
            items = flush.get('result', [])
            for item in items:
                msg = item.get('message') or {}
                msg_time = msg.get('date', 0)
                if (time.time() - msg_time < 120) and str(msg.get('chat', {}).get('id')) == str(self._chat):
                    t = msg.get('text') or msg.get('caption') or ''
                    if match(t):
                        return t
            if items:
                offset = items[-1]['update_id'] + 1
        except (requests.RequestException, ValueError, KeyError):
            pass
        end = time.time() + timeout
        while time.time() < end:
            if stop_event and stop_event.is_set():
                break
            params = {'timeout': 2}
            if offset is not None:
                params['offset'] = offset
            try:
                data = requests.get(base, params=params, timeout=10).json()
            except (requests.RequestException, ValueError):
                time.sleep(1)
                continue
            for upd in data.get('result', []):
                offset = upd['update_id'] + 1
                msg = upd.get('message') or {}
                if str(msg.get('chat', {}).get('id')) != str(self._chat):
                    continue
                text = msg.get('text') or msg.get('caption') or ''
                if match(text):
                    return text
        return None


def in_window(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return WINDOW_START <= (now.hour, now.minute) < WINDOW_END


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        STILL_ACTIVE = 259
        exit_code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return exit_code.value == STILL_ACTIVE
    except Exception:
        return True


def lock_active() -> bool:
    try:
        if not LOCK_PATH.exists():
            return False
        mtime_fresh = (time.time() - LOCK_PATH.stat().st_mtime) < LOCK_FRESH_SECONDS
        try:
            pid = int(LOCK_PATH.read_text().strip())
            if not _is_pid_alive(pid):
                return False
        except (ValueError, OSError):
            pass
        return mtime_fresh
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
    port = parsed.port or 5000
    url = auth.get_login_url()

    print("\n" + "=" * 64, flush=True)
    print("              FYERS AUTHENTICATION REQUIRED", flush=True)
    print("=" * 64, flush=True)
    print(f"Opening login URL in your browser:\n{url}\n", flush=True)
    print("HOW TO COMPLETE LOGIN:", flush=True)
    print("1. Log in with your FYERS credentials in the browser tab that opens.", flush=True)
    print("2. If redirected to http://127.0.0.1, it will be captured automatically.", flush=True)
    print("3. If redirected to https://127.0.0.1 (showing connection error / blank):", flush=True)
    print("   -> Copy the address-bar URL or auth_code,", flush=True)
    print("   -> Paste it here in this window and press ENTER, OR", flush=True)
    print("   -> Send the URL or auth_code to your Telegram bot.", flush=True)
    print("-" * 64, flush=True)
    print("Waiting for login (via browser redirect, console paste, or Telegram)...", flush=True)
    print("Paste redirected URL or auth_code here: ", end="", flush=True)

    try:
        webbrowser.open(url)
    except Exception:
        pass

    code_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()

    # 1. Local HTTP Redirect Server
    def _local_server_worker() -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    code = parse_auth_code(self.path)
                    code_queue.put(('browser redirect', code))
                    self.send_response(200)
                    self.send_header('Content-type', 'text/html; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(
                        b'<html><body style="font-family:sans-serif;text-align:center;padding:50px;">'
                        b'<h2 style="color:#10b981;">&#10004; NAT RAS Login Successful!</h2>'
                        b'<p>Authentication received. You may now close this tab and return to the application.</p>'
                        b'</body></html>'
                    )
                except Exception:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'NAT RAS: Could not parse auth_code from request.')

            def log_message(self, *args):
                pass

        try:
            server = HTTPServer(('127.0.0.1', port), Handler)
            server.timeout = 1.0
            while not stop_event.is_set():
                server.handle_request()
            server.server_close()
        except OSError:
            log.debug("Local redirect server port %d unavailable or already in use.", port)

    # 2. Laptop Console Reader
    def _console_reader_worker() -> None:
        while not stop_event.is_set():
            try:
                line = sys.stdin.readline()
                if not line:
                    time.sleep(0.5)
                    continue
                line = line.strip()
                if not line:
                    continue
                code = parse_auth_code(line)
                code_queue.put(('laptop console', code))
                return
            except FyersAuthError:
                print("\n[NAT RAS] Could not extract auth_code from entered text.")
                print("Please copy the FULL address-bar URL and paste again: ", end="", flush=True)
            except Exception:
                break

    # 3. Telegram Notifier Listener
    def _telegram_worker() -> None:
        if not notifier.enabled:
            return
        notifier.send(
            'NAT RAS: FYERS login needed.\n'
            '1) Open this link and log in:\n' + url + '\n\n'
            '2) After login, copy the address-bar URL (or auth_code) and send it here '
            '(or paste it into your laptop console).'
        )
        while not stop_event.is_set():
            text = notifier.wait_for_message(
                lambda t: bool(t and t.strip()),
                timeout=LOGIN_TIMEOUT_SECONDS,
                stop_event=stop_event,
            )
            if not text or stop_event.is_set():
                break
            try:
                code = parse_auth_code(text)
                code_queue.put(('Telegram chat', code))
                return
            except FyersAuthError:
                notifier.send(
                    'NAT RAS: Could not extract a valid FYERS auth_code from your message.\n'
                    'Please copy and send the FULL URL from the browser address bar.'
                )

    threads = [
        threading.Thread(target=_local_server_worker, daemon=True, name="fyers-redirect-server"),
        threading.Thread(target=_console_reader_worker, daemon=True, name="fyers-console-reader"),
        threading.Thread(target=_telegram_worker, daemon=True, name="fyers-telegram-worker"),
    ]
    for t in threads:
        t.start()

    try:
        source, code = code_queue.get(timeout=LOGIN_TIMEOUT_SECONDS)
        stop_event.set()
    except queue.Empty:
        stop_event.set()
        msg = 'NAT RAS: FYERS login timed out (15 min). Collection NOT started.'
        print(f"\n[NAT RAS] {msg}", flush=True)
        notifier.send(msg)
        return False

    try:
        print(f"\n[NAT RAS] Received auth code via {source}. Exchanging for access token...", flush=True)
        auth.exchange_auth_code(code)
    except FyersAuthError as exc:
        msg = f'NAT RAS: FYERS token exchange failed: {exc}'
        print(f"\n[NAT RAS] {msg}", flush=True)
        notifier.send(msg)
        return False

    success_msg = 'NAT RAS: FYERS login successful. Starting data collection & paper engine.'
    print(f"\n[NAT RAS] {success_msg}\n", flush=True)
    notifier.send(success_msg)
    return True


def run_collector(notifier: Notifier, force: bool = False) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    healer = AutoHealer.get_instance()
    healer.telegram_notifier = notifier.send
    restarts = 0
    print("Launching collector, paper engine & market intelligence...", flush=True)
    while True:
        if not force and not in_window(datetime.now(IST)):
            break

        # Proactively check internet before launching
        if not healer.check_internet():
            healer.wait_for_network_recovery(max_wait_seconds=300.0)

        with open(LOG_PATH.parent / 'collector.log', 'a', encoding='utf-8') as out:
            proc = subprocess.Popen(
                [sys.executable, '-m', 'natras.cli.collect'], stdout=out, stderr=subprocess.STDOUT
            )
            # Supervisor watchdog loop: check every 10 seconds
            while proc.poll() is None:
                touch_lock()
                # Check for frozen / stalled process via heartbeat
                is_fresh, age = healer.is_heartbeat_healthy(max_age_seconds=180.0)
                if age > 180.0 and in_window(datetime.now(IST)):
                    healer.record_incident(
                        category="FEED_STALL",
                        action_taken=f"Terminated frozen collector process PID {proc.pid} (heartbeat age {age:.0f}s) and auto-restarting",
                        details="Collector process became unresponsive during market hours",
                        severity="WARNING",
                        notify_telegram=True,
                    )
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    break
                time.sleep(10)

        if proc.returncode == 0:
            break

        restarts += 1
        if proc.returncode in (4294967295, -1):
            details = "Collector process was recycled/restarted."
            sev = "INFO"
        else:
            details = f"Collector process exited with return code {proc.returncode}."
            sev = "WARNING"

        healer.record_incident(
            category="PROCESS_RECYCLED",
            action_taken=f"Auto-respawning fresh collector process (restart {restarts})",
            details=details,
            severity=sev,
            notify_telegram=True,
        )

        if restarts > MAX_COLLECTOR_RESTARTS:
            notifier.send('NAT RAS: Maximum supervisor restarts reached. Manual check advised in data/logs/collector.log.')
            return

        time.sleep(min(15 * restarts, 60))


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
    try:
        if not wait_for_internet():
            log.error('No internet; exiting.')
            return 1
        notifier.send('NAT RAS: laptop online. Checking FYERS session.')
        if not ensure_fyers_token(notifier):
            return 1
        run_collector(notifier, force=force)
        end_of_day_summary(notifier)
        return 0
    finally:
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass


if __name__ == '__main__':
    raise SystemExit(main())
