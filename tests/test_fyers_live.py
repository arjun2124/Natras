from datetime import datetime

import pytest

from natras.adapters.fyers_live import (
    FyersDataError,
    FyersFeed,
    FyersLiveAdapter,
    _date_chunks,
    message_to_tick,
)
from natras.data.candle_engine import InvalidCandleError
from natras.data.session import IST

SYM = 'NSE:SBIN-EQ'


def epoch(day, h, m, s=0):
    return datetime(2026, 10, day, h, m, s, tzinfo=IST).timestamp()


class FakeClient:
    def __init__(self, history=None, quotes=None, profile=None):
        self._history = list(history or [])
        self._quotes = quotes
        self._profile = profile or {'s': 'ok'}
        self.history_calls = []

    def history(self, data):
        self.history_calls.append(data)
        return self._history.pop(0) if self._history else {'s': 'no_data'}

    def quotes(self, data):
        return self._quotes

    def get_profile(self):
        return self._profile


def adapter(client):
    return FyersLiveAdapter('app', 'tok', lambda a, t: client)


NOW = datetime(2026, 10, 14, 10, 0, tzinfo=IST)


def test_fetch_candles_parses_and_validates():
    row = [epoch(13, 9, 15), 100, 105, 99, 102, 1000]
    client = FakeClient([{'s': 'ok', 'candles': [row]}])
    candles = adapter(client).fetch_candles(SYM, '5', '2026-10-13', '2026-10-13', now=NOW)
    assert len(candles) == 1
    assert candles[0].close == 102
    assert candles[0].timestamp == datetime(2026, 10, 13, 9, 15, tzinfo=IST)


def test_incomplete_candle_dropped():
    rows = [[epoch(14, 9, 50), 1, 2, 1, 2, 5], [epoch(14, 9, 55), 1, 2, 1, 2, 5]]
    client = FakeClient([{'s': 'ok', 'candles': rows}])
    now = datetime(2026, 10, 14, 9, 58, tzinfo=IST)
    candles = adapter(client).fetch_candles(SYM, '5', '2026-10-14', '2026-10-14', now=now)
    assert [c.timestamp.minute for c in candles] == [50]


def test_no_data_returns_empty():
    client = FakeClient([{'s': 'no_data'}])
    assert adapter(client).fetch_candles(SYM, '5', '2026-10-13', '2026-10-13', now=NOW) == []


def test_error_response_raises():
    client = FakeClient([{'s': 'error', 'code': -300, 'message': 'bad symbol'}])
    with pytest.raises(FyersDataError):
        adapter(client).fetch_candles(SYM, '5', '2026-10-13', '2026-10-13', now=NOW)


def test_invalid_ohlc_row_raises():
    row = [epoch(13, 9, 15), 100, 90, 99, 95, 10]
    client = FakeClient([{'s': 'ok', 'candles': [row]}])
    with pytest.raises(InvalidCandleError):
        adapter(client).fetch_candles(SYM, '5', '2026-10-13', '2026-10-13', now=NOW)


def test_wrong_resolution_refused():
    with pytest.raises(ValueError):
        adapter(FakeClient()).fetch_candles(SYM, '15', '2026-10-13', '2026-10-13', now=NOW)


def test_history_is_chunked():
    client = FakeClient()
    adapter(client).fetch_candles(SYM, '5', '2026-01-01', '2026-10-13', now=NOW)
    calls = client.history_calls
    assert len(calls) >= 3
    assert calls[0]['range_from'] == '2026-01-01' and calls[-1]['range_to'] == '2026-10-13'
    chunks = _date_chunks(datetime(2026, 1, 1).date(), datetime(2026, 10, 13).date())
    assert all((e - s).days + 1 <= 90 for s, e in chunks)
    assert all((chunks[i + 1][0] - chunks[i][1]).days == 1 for i in range(len(chunks) - 1))


def test_quotes_and_missing_symbol():
    quotes = {'s': 'ok', 'd': [{'n': SYM, 's': 'ok', 'v': {'lp': 500.5}}]}
    assert adapter(FakeClient(quotes=quotes)).fetch_quotes([SYM]) == {SYM: {'lp': 500.5}}
    with pytest.raises(FyersDataError):
        adapter(FakeClient(quotes=quotes)).fetch_quotes([SYM, 'NSE:XYZ-EQ'])


def test_authenticate_failure():
    with pytest.raises(FyersDataError):
        adapter(FakeClient(profile={'s': 'error', 'code': -16})).authenticate()


def msg(price=500.5, cum=1000, ts=None):
    m = {'symbol': SYM, 'ltp': price, 'type': 'sf', 'last_traded_time': ts or epoch(14, 9, 15, 5)}
    if cum is not None:
        m['vol_traded_today'] = cum
    return m


def test_message_to_tick_volume_deltas():
    tick, cum = message_to_tick(msg(cum=1000), None)
    assert tick.volume == 0.0 and cum == 1000.0
    tick, cum = message_to_tick(msg(cum=1000), 900.0)
    assert tick.volume == 100.0
    tick, cum = message_to_tick(msg(cum=50), 2000.0)
    assert tick.volume == 0.0 and cum == 50.0


def test_message_to_tick_ignores_control_and_untimed():
    assert message_to_tick({'type': 'cn', 's': 'ok', 'code': 200}, None) is None
    untimed = {'symbol': SYM, 'ltp': 1.0}
    assert message_to_tick(untimed, None) is None


def test_feed_emits_candle_via_callback():
    got = []
    feed = FyersFeed('app', 'tok', [SYM], got.append)
    feed.handle_message(msg(100.0, 10, epoch(14, 9, 15, 5)))
    feed.handle_message(msg(105.0, 25, epoch(14, 9, 17, 0)))
    feed.handle_message(msg(103.0, 30, epoch(14, 9, 20, 1)))
    assert len(got) == 1
    assert (got[0].open, got[0].high, got[0].close, got[0].volume) == (100.0, 105.0, 105.0, 15.0)
    assert feed.healthy


def test_feed_marks_unhealthy_on_bad_tick():
    feed = FyersFeed('app', 'tok', [SYM], lambda c: None)
    feed.handle_message(msg(-5.0))
    assert not feed.healthy and feed.last_error


def test_feed_subscribes_on_open():
    class FakeSocket:
        def __init__(self):
            self.subscribed = None
            self.connected = False

        def connect(self):
            self.connected = True

        def subscribe(self, symbols, data_type):
            self.subscribed = (symbols, data_type)

        def close_connection(self):
            pass

    sock = FakeSocket()
    captured = {}

    def factory(app_id, token, **kwargs):
        captured.update(kwargs)
        return sock

    feed = FyersFeed('app', 'tok', [SYM], lambda c: None, factory)
    feed.start()
    captured['on_open']()
    assert sock.connected and sock.subscribed == ([SYM], 'SymbolUpdate')
