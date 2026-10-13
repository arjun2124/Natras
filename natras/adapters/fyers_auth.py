'''FYERS API v3 authentication (auth-code flow) and local token storage.

Secrets come only from environment variables. Tokens are written to a git-ignored
file and are never logged or printed. Requires `pip install fyers-apiv3`.

Assumption (verify live): FYERS access tokens expire around 06:00 IST daily.
'''

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import parse_qs, urlparse

from natras.data.session import IST

DEFAULT_TOKEN_PATH = Path('data') / 'fyers_token.json'
ENV_APP_ID = 'FYERS_APP_ID'
ENV_SECRET = 'FYERS_SECRET_KEY'
ENV_REDIRECT = 'FYERS_REDIRECT_URI'


class FyersAuthError(RuntimeError):
    '''Raised for any authentication failure. Messages never contain secrets.'''


def _last_reset(now: datetime) -> datetime:
    local = now.astimezone(IST)
    reset = local.replace(hour=6, minute=0, second=0, microsecond=0)
    return reset if local >= reset else reset - timedelta(days=1)


def parse_auth_code(redirected_url: str) -> str:
    '''Extract auth_code from the URL FYERS redirects to after login.'''
    query = parse_qs(urlparse(redirected_url).query)
    values = query.get('auth_code')
    if not values or not values[0]:
        raise FyersAuthError('No auth_code found in the redirected URL.')
    return values[0]


@dataclass(frozen=True)
class FyersCredentials:
    app_id: str
    secret_key: str = field(repr=False)
    redirect_uri: str

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> 'FyersCredentials':
        source = os.environ if env is None else env
        names = (ENV_APP_ID, ENV_SECRET, ENV_REDIRECT)
        missing = [n for n in names if not source.get(n)]
        if missing:
            raise FyersAuthError('Missing environment variables: ' + ', '.join(missing))
        return cls(source[ENV_APP_ID], source[ENV_SECRET], source[ENV_REDIRECT])


class TokenStore:
    def __init__(self, path: Path = DEFAULT_TOKEN_PATH) -> None:
        self.path = Path(path)

    def save(self, access_token: str, issued_at: Optional[datetime] = None) -> None:
        issued = (issued_at or datetime.now(IST)).astimezone(IST)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({'access_token': access_token, 'issued_at': issued.isoformat()}))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def load_fresh_token(self, now: Optional[datetime] = None) -> Optional[str]:
        try:
            data = json.loads(self.path.read_text())
            issued = datetime.fromisoformat(data['issued_at'])
            token = data['access_token']
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if issued.tzinfo is None or not token:
            return None
        current = (now or datetime.now(IST)).astimezone(IST)
        return token if issued >= _last_reset(current) else None


def _default_session_factory(creds: FyersCredentials) -> Any:
    try:
        from fyers_apiv3 import fyersModel
    except ImportError as exc:
        raise FyersAuthError('fyers-apiv3 is not installed. Run: pip install fyers-apiv3') from exc
    return fyersModel.SessionModel(
        client_id=creds.app_id,
        secret_key=creds.secret_key,
        redirect_uri=creds.redirect_uri,
        response_type='code',
        grant_type='authorization_code',
    )


class FyersAuthenticator:
    def __init__(
        self,
        credentials: FyersCredentials,
        store: Optional[TokenStore] = None,
        session_factory: Optional[Callable[[FyersCredentials], Any]] = None,
    ) -> None:
        self._creds = credentials
        self._store = store or TokenStore()
        self._factory = session_factory or _default_session_factory

    def get_login_url(self) -> str:
        return self._factory(self._creds).generate_authcode()

    def exchange_auth_code(self, auth_code: str) -> str:
        session = self._factory(self._creds)
        session.set_token(auth_code)
        resp = session.generate_token()
        if not isinstance(resp, dict) or resp.get('s') != 'ok' or not resp.get('access_token'):
            code = resp.get('code') if isinstance(resp, dict) else None
            raise FyersAuthError(f'FYERS token exchange failed (code={code}).')
        token = resp['access_token']
        self._store.save(token)
        return token

    def get_access_token(self, now: Optional[datetime] = None) -> str:
        token = self._store.load_fresh_token(now)
        if token is None:
            raise FyersAuthError('No fresh FYERS token. Run: python -m natras.cli.fyers_login')
        return token
