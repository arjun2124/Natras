'''Upstox capability audit. Run: python -m natras.cli.upstox_audit

Reads UPSTOX_ACCESS_TOKEN from the environment. The token is never written anywhere.
Endpoint paths should be checked against current Upstox docs; a FAIL may mean a wrong path.
'''
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

BASE = 'https://api.upstox.com'
OUT = Path('runtime_data') / 'reports' / 'upstox'
NIFTY = 'NSE_INDEX|Nifty 50'


def probe(name, session, path):
    try:
        r = session.get(BASE + path, timeout=20)
        ctype = r.headers.get('content-type', '')
        body = r.json() if ctype.startswith('application/json') else {}
        ok = r.status_code == 200
        return {
            'probe': name,
            'http': r.status_code,
            'ok': ok,
            'detail': 'ok' if ok else str(body)[:300],
            'sample_rows': len(body.get('data', {}).get('candles', [])) if ok else None,
        }
    except Exception as exc:
        return {'probe': name, 'ok': False, 'detail': type(exc).__name__}


def main() -> int:
    token = os.environ.get('UPSTOX_ACCESS_TOKEN')
    if not token:
        raise SystemExit('Set UPSTOX_ACCESS_TOKEN in your environment.')
    s = requests.Session()
    s.headers.update({'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
    end = date.today()
    start = end - timedelta(days=5)
    results = [
        probe('profile', s, '/v2/user/profile'),
        probe('spot_5m_v3', s, f'/v3/historical-candle/{NIFTY}/minutes/5/{end}/{start}'),
        probe('spot_1m_v3', s, f'/v3/historical-candle/{NIFTY}/minutes/1/{end}/{start}'),
        probe('spot_5m_deep', s, f'/v3/historical-candle/{NIFTY}/minutes/5/2022-02-01/2022-01-01'),
        probe('expiries', s, f'/v2/expired-instruments/expiries?instrument_key={NIFTY}'),
    ]
    report = {
        'generated_at': datetime.now().isoformat(),
        'results': results,
        'note': 'Greeks/IV/OI availability must be checked separately per contract.',
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'capability_report.json').write_text(json.dumps(report, indent=2))
    lines = ['# Upstox capability report', ''] + [
        f"- {r['probe']}: {'OK' if r.get('ok') else 'FAIL'} ({r.get('detail')})" for r in results
    ]
    (OUT / 'capability_report.md').write_text('\n'.join(lines))
    print('Report written to', OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
