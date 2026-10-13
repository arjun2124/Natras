'''FYERS login. Run: python -m natras.cli.fyers_auto_login

http redirect URI  -> catches the redirect automatically on the local port.
https redirect URI -> the browser shows a connection error page; paste the address-bar URL.
'''
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from natras.adapters.fyers_auth import FyersAuthenticator, FyersCredentials, parse_auth_code
from natras.cli._env import load_env


def wait_for_redirect(port: int, timeout: int = 300) -> str:
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            result['url'] = f'http://127.0.0.1:{port}{self.path}'
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'NAT RAS: login received. You can close this tab.')

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', port), Handler)
    server.timeout = timeout
    t = threading.Thread(target=server.handle_request)
    t.start()
    t.join(timeout + 5)
    server.server_close()
    if 'url' not in result:
        raise TimeoutError('No FYERS redirect received.')
    return result['url']


_wait_for_redirect = wait_for_redirect


def main() -> int:
    load_env()
    creds = FyersCredentials.from_env()
    parsed = urlparse(creds.redirect_uri)
    auth = FyersAuthenticator(creds)
    url = auth.get_login_url()
    print('Opening FYERS login in your browser...')
    webbrowser.open(url)
    if parsed.scheme == 'http':
        redirected = wait_for_redirect(parsed.port or 5000)
    else:
        redirected = input('After login, paste the full URL from the address bar: ').strip()
    auth.exchange_auth_code(parse_auth_code(redirected))
    print('Token saved (value not shown).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
