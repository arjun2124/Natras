from datetime import datetime

import pytest

from natras.adapters.fyers_auth import (
    FyersAuthError,
    FyersAuthenticator,
    FyersCredentials,
    TokenStore,
    parse_auth_code,
)
from natras.data.session import IST


def test_parse_auth_code():
    url = 'http://127.0.0.1/?s=ok&code=200&auth_code=ABC123&state=x'
    assert parse_auth_code(url) == 'ABC123'
    assert parse_auth_code('https://127.0.0.1:5000/callback?s=ok&code=200&auth_code=XYZ789') == 'XYZ789'
    assert parse_auth_code('127.0.0.1:5000/callback?s=ok&auth_code=XYZ789') == 'XYZ789'
    assert parse_auth_code('auth_code=ABC123') == 'ABC123'
    assert parse_auth_code('auth_code = ABC123') == 'ABC123'
    assert parse_auth_code('auth_code: ABC123') == 'ABC123'
    assert parse_auth_code('?auth_code=ABC123&state=x') == 'ABC123'
    assert parse_auth_code('https://127.0.0.1:5000/callback#auth_code=ABC123') == 'ABC123'
    assert parse_auth_code('"https://127.0.0.1:5000/callback?auth_code=ABC123"') == 'ABC123'
    assert parse_auth_code('<https://127.0.0.1:5000/callback?auth_code=ABC123>') == 'ABC123'
    assert parse_auth_code('Here is URL: https://127.0.0.1:5000/callback?auth_code=ABC123 done') == 'ABC123'
    jwt_sample = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.samplepayload.sig'
    assert parse_auth_code(jwt_sample) == jwt_sample
    with pytest.raises(FyersAuthError):
        parse_auth_code('http://127.0.0.1/?s=ok')
    with pytest.raises(FyersAuthError):
        parse_auth_code('')
    with pytest.raises(FyersAuthError):
        parse_auth_code('hello world')


def test_credentials_missing_lists_names_only():
    with pytest.raises(FyersAuthError) as exc:
        FyersCredentials.from_env({'FYERS_APP_ID': 'abc'})
    msg = str(exc.value)
    assert 'FYERS_SECRET_KEY' in msg and 'FYERS_REDIRECT_URI' in msg and 'abc' not in msg


def test_credentials_repr_hides_secret():
    creds = FyersCredentials.from_env({
        'FYERS_APP_ID': 'app', 'FYERS_SECRET_KEY': 'topsecret', 'FYERS_REDIRECT_URI': 'http://x',
    })
    assert 'topsecret' not in repr(creds)


def test_token_freshness(tmp_path):
    store = TokenStore(tmp_path / 't.json')
    store.save('tok', issued_at=datetime(2026, 10, 14, 7, 0, tzinfo=IST))
    assert store.load_fresh_token(datetime(2026, 10, 14, 12, 0, tzinfo=IST)) == 'tok'
    assert store.load_fresh_token(datetime(2026, 10, 15, 7, 0, tzinfo=IST)) is None
    store.save('old', issued_at=datetime(2026, 10, 14, 5, 0, tzinfo=IST))
    assert store.load_fresh_token(datetime(2026, 10, 14, 12, 0, tzinfo=IST)) is None


def test_missing_or_corrupt_token_file(tmp_path):
    path = tmp_path / 't.json'
    assert TokenStore(path).load_fresh_token() is None
    path.write_text('not json')
    assert TokenStore(path).load_fresh_token() is None


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.code = None

    def set_token(self, code):
        self.code = code

    def generate_token(self):
        return self.response

    def generate_authcode(self):
        return 'https://login.example/auth'


def make_auth(tmp_path, response):
    creds = FyersCredentials('app', 'sec', 'http://x')
    store = TokenStore(tmp_path / 't.json')
    return FyersAuthenticator(creds, store, lambda c: FakeSession(response)), store


def test_exchange_saves_token(tmp_path):
    auth, store = make_auth(tmp_path, {'s': 'ok', 'access_token': 'tok123'})
    assert auth.exchange_auth_code('code') == 'tok123'
    assert auth.get_access_token() == 'tok123'
    assert auth.get_login_url() == 'https://login.example/auth'


def test_exchange_failure_raises_without_token(tmp_path):
    auth, store = make_auth(tmp_path, {'s': 'error', 'code': -1, 'message': 'bad'})
    with pytest.raises(FyersAuthError):
        auth.exchange_auth_code('code')
    with pytest.raises(FyersAuthError):
        auth.get_access_token()
