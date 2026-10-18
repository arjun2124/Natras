'''FYERS API v3 live DATA adapter (quotes, history, WebSocket feed). No order placement.

STATUS: IMPLEMENTED_UNVERIFIED_LIVE. Unit-tested with fakes only; it has not been
verified against a live FYERS session. Requires `pip install fyers-apiv3`.
The older NOT_IMPLEMENTED stub in fyers.py is intentionally left untouched.
'''

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from natras.config.constants import CANDLE_TIMEFRAME_MINUTES
from natras.data.candle_engine import Candle, validate_candle
from natras.data.session import IST
from natras.data.tick_aggregator import Tick, TickAggregator

logger = logging.getLogger(__name__)

HISTORY_CHUNK_DAYS = 90
STATUS = 'IMPLEMENTED_UNVERIFIED_LIVE'


class FyersDataError(RuntimeError):
    '''Raised when FYERS returns an error or malformed data.'''


class FyersSdkMissingError(ImportError):
    '''Raised when fyers-apiv3 is needed but not installed.'''


def _default_model_factory(app_id: str, access_token: str) -> Any:
    try:
        from fyers_apiv3 import fyersModel
    except ImportError as exc:
        raise FyersSdkMissingError('fyers-apiv3 is not installed. Run: pip install fyers-apiv3') from exc
    return fyersModel.FyersModel(client_id=app_id, token=access_token, is_async=False, log_path='')


def _default_socket_factory(app_id, access_token, on_message, on_error, on_close, on_open):
    try:
        from fyers_apiv3.FyersWebsocket import data_ws
    except ImportError as exc:
        raise FyersSdkMissingError('fyers-apiv3 is not installed. Run: pip install fyers-apiv3') from exc
    return data_ws.FyersDataSocket(
        access_token=f'{app_id}:{access_token}',
        log_path='',
        litemode=False,
        write_to_file=False,
        reconnect=True,
        on_connect=on_open,
        on_close=on_close,
        on_error=on_error,
        on_message=on_message,
    )


def _require_ok(resp: Any, what: str) -> Dict[str, Any]:
    if not isinstance(resp, dict) or resp.get('s') != 'ok':
        code = resp.get('code') if isinstance(resp, dict) else None
        message = resp.get('message') if isinstance(resp, dict) else None
        raise FyersDataError(f'FYERS {what} request failed: code={code}, message={message}')
    return resp


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _date_chunks(start: date, end: date, max_days: int = HISTORY_CHUNK_DAYS) -> List[Tuple[date, date]]:
    if start > end:
        raise ValueError('range_from must not be after range_to.')
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=max_days - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


class FyersLiveAdapter:
    status = STATUS

    def __init__(self, app_id: str, access_token: str, model_factory: Optional[Callable[[str, str], Any]] = None) -> None:
        self._app_id = app_id
        self._access_token = access_token
        self._model_factory = model_factory or _default_model_factory
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._model_factory(self._app_id, self._access_token)
        return self._client

    def is_implemented(self) -> bool:
        return True

    def get_status(self) -> Dict[str, Any]:
        return {
            'adapter': 'FYERS',
            'status': STATUS,
            'implemented': True,
            'live_verified': False,
            'message': 'Data only. Not yet verified against a live FYERS session.',
        }

    def authenticate(self) -> None:
        '''Verify the access token by fetching the profile.'''
        _require_ok(self.client.get_profile(), 'profile')

    def fetch_quotes(self, symbols: Sequence[str]) -> Dict[str, Any]:
        resp = _require_ok(self.client.quotes({'symbols': ','.join(symbols)}), 'quotes')
        out: Dict[str, Any] = {}
        for item in resp.get('d', []):
            if isinstance(item, dict) and item.get('s') == 'ok' and 'v' in item:
                out[item['n']] = item['v']
        missing = [s for s in symbols if s not in out]
        if missing:
            raise FyersDataError('No quote returned for: ' + ', '.join(missing))
        return out

    def fetch_candles(
        self,
        symbol: str,
        resolution: str,
        range_from: Any,
        range_to: Any,
        now: Optional[datetime] = None,
    ) -> List[Candle]:
        '''Historical completed 5-minute candles. The still-forming candle is dropped.'''
        if str(resolution) != str(CANDLE_TIMEFRAME_MINUTES):
            raise ValueError(f'Platform is locked to {CANDLE_TIMEFRAME_MINUTES}-minute candles.')
        current = (now or datetime.now(IST)).astimezone(IST)
        delta = timedelta(minutes=CANDLE_TIMEFRAME_MINUTES)
        found: Dict[datetime, Candle] = {}
        chunks = list(_date_chunks(_to_date(range_from), _to_date(range_to)))
        for idx, (chunk_start, chunk_end) in enumerate(chunks):
            if idx > 0:
                time.sleep(0.02)  # Throttle to stay safely within FYERS rate limits (50 req/min)
            resp = self.client.history({
                'symbol': symbol,
                'resolution': str(resolution),
                'date_format': '1',
                'range_from': chunk_start.isoformat(),
                'range_to': chunk_end.isoformat(),
                'cont_flag': '1',
            })
            if isinstance(resp, dict) and resp.get('s') == 'no_data':
                continue
            _require_ok(resp, 'history')
            for row in resp.get('candles', []):
                if not isinstance(row, (list, tuple)) or len(row) not in (6, 7):
                    raise FyersDataError(f'Malformed candle row: {row!r}')
                ts = datetime.fromtimestamp(row[0], IST)
                candle = validate_candle(symbol, ts, row[1], row[2], row[3], row[4], row[5])
                if candle.timestamp + delta <= current:
                    found[candle.timestamp] = candle
        return [found[k] for k in sorted(found)]

    def subscribe_quotes(self, symbols: List[str]) -> None:
        raise NotImplementedError('Use FyersFeed for streaming; it aggregates ticks into candles.')


def message_to_tick(msg: Any, prev_cumulative: Optional[float]) -> Optional[Tuple[Tick, Optional[float]]]:
    '''Convert a FYERS WebSocket message to (Tick, new_cumulative_volume).

    Returns None for control messages and for messages with no exchange timestamp
    (the local clock is never substituted). The first tick after connect gets volume
    0 because the traded-so-far baseline is unknown, so the first bucket after a
    (re)connect may under-count volume.
    '''
    if not isinstance(msg, dict) or 'symbol' not in msg or 'ltp' not in msg:
        return None
    epoch = msg.get('last_traded_time') or msg.get('exch_feed_time')
    if not epoch:
        return None
    cumulative = msg.get('vol_traded_today')
    if cumulative is None:
        delta, new_cum = 0.0, prev_cumulative
    elif prev_cumulative is None:
        delta, new_cum = 0.0, float(cumulative)
    elif cumulative < prev_cumulative:
        logger.warning('Cumulative volume reset for %s; treating delta as 0.', msg.get('symbol'))
        delta, new_cum = 0.0, float(cumulative)
    else:
        delta, new_cum = float(cumulative) - prev_cumulative, float(cumulative)
    tick = Tick(msg['symbol'], datetime.fromtimestamp(epoch, IST), float(msg['ltp']), delta)
    return tick, new_cum


class FyersFeed:
    '''WebSocket feed that aggregates ticks into completed candles per symbol.'''

    def __init__(
        self,
        app_id: str,
        access_token: str,
        symbols: Sequence[str],
        on_candle: Callable[[Candle], None],
        socket_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        if not symbols:
            raise ValueError('At least one symbol is required.')
        self._app_id = app_id
        self._access_token = access_token
        self.symbols = list(symbols)
        self._on_candle = on_candle
        self._factory = socket_factory or _default_socket_factory
        self._aggregators = {s: TickAggregator(s) for s in self.symbols}
        self._cumulative: Dict[str, Optional[float]] = {s: None for s in self.symbols}
        self._socket: Any = None
        self.healthy = True
        self.last_error: Optional[str] = None

    def start(self) -> None:
        self._socket = self._factory(
            self._app_id, self._access_token,
            on_message=self.handle_message, on_error=self._on_error,
            on_close=self._on_close, on_open=self._on_open,
        )
        self._socket.connect()

    def stop(self) -> None:
        if self._socket is not None:
            self._socket.close_connection()

    def _on_open(self) -> None:
        self._socket.subscribe(symbols=self.symbols, data_type='SymbolUpdate')

    def _on_error(self, error: Any) -> None:
        self.healthy = False
        self.last_error = f'socket error: {type(error).__name__}'
        logger.error('FYERS socket error (%s).', type(error).__name__)

    def _on_close(self, message: Any = None) -> None:
        logger.warning('FYERS socket closed.')

    def handle_message(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        symbol = msg.get('symbol')
        if symbol not in self._aggregators:
            return
        try:
            parsed = message_to_tick(msg, self._cumulative[symbol])
            if parsed is None:
                return
            tick, new_cum = parsed
            self._cumulative[symbol] = new_cum
            candle = self._aggregators[symbol].add_tick(tick)
            if candle is not None:
                self._on_candle(candle)
        except Exception as exc:
            self.healthy = False
            self.last_error = f'{type(exc).__name__}: {exc}'
            logger.error('Feed processing error for %s: %s', symbol, self.last_error)

    def close_completed(self, now: Optional[datetime] = None) -> None:
        '''Call periodically so a bucket closes on time even if no new tick arrives.'''
        current = now or datetime.now(IST)
        for agg in self._aggregators.values():
            candle = agg.close_if_complete(current)
            if candle is not None:
                self._on_candle(candle)

    def get_status(self) -> Dict[str, Any]:
        return {
            'healthy': self.healthy,
            'last_error': self.last_error,
            'symbols': {s: a.get_status() for s, a in self._aggregators.items()},
        }
