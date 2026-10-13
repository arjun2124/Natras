'''Interactive FYERS login. Run: python -m natras.cli.fyers_login

Prints the login URL, asks you to paste the redirected URL, then stores the token
in data/fyers_token.json (git-ignored). The token value is never printed.
'''

from natras.adapters.fyers_auth import (
    DEFAULT_TOKEN_PATH,
    FyersAuthenticator,
    FyersCredentials,
    parse_auth_code,
)


def main() -> int:
    auth = FyersAuthenticator(FyersCredentials.from_env())
    print('Open this URL, log in, then paste the full redirected URL below:')
    print(auth.get_login_url())
    redirected = input('Redirected URL: ').strip()
    auth.exchange_auth_code(parse_auth_code(redirected))
    print(f'Token saved to {DEFAULT_TOKEN_PATH} (value not shown).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
