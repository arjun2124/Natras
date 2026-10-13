'''Tick to completed 5-minute candle aggregation.

Rules (see GEMINI.md): a candle is emitted only when its bucket is fully closed,
gaps are recorded and never filled, and nothing is fabricated. Candle timestamp
is the bucket START time (IST), matching FYERS historical candles.
'''

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from natras.config.constants import CANDLE_TIMEFRAME_MINUTES
from natras.data.candle_engine import Candle, validate_candle
from natras.data.session import bucket_start, is_market_open, to_ist

logger = logging.getLogger(__name__)


class InvalidTickError(ValueError):
    '''Raised when a tick is structurally invalid.'''


@dataclass(frozen=True)
class Tick:
    symbol: str
    timestamp: datetime
    price: float
    volume: float = 0.0  # incremental traded quantity for this tick


@dataclass
class _Bucket:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class TickAggregator:
    '''Builds completed candles for one symbol from an ordered tick stream.'''

    def __init__(self, symbol: str, timeframe_minutes: int = CANDLE_TIMEFRAME_MINUTES) -> None:
        self.symbol = symbol
        self.timeframe = timeframe_minutes
        self._delta = timedelta(minutes=timeframe_minutes)
        self._bucket: Optional[_Bucket] = None
        self._last_closed_start: Optional[datetime] = None
        self.gaps: List[Tuple[datetime, datetime]] = []
        self.late_ticks_dropped = 0
        self.off_session_ticks_dropped = 0

    def _validate(self, tick: Tick) -> None:
        if tick.symbol != self.symbol:
            raise InvalidTickError(f'Tick symbol {tick.symbol!r} does not match aggregator {self.symbol!r}.')
        if tick.timestamp.tzinfo is None:
            raise InvalidTickError('Tick timestamp must be timezone-aware.')
        if not isinstance(tick.price, (int, float)) or not math.isfinite(tick.price) or tick.price <= 0:
            raise InvalidTickError(f'Tick price {tick.price!r} must be a finite positive number.')
        if not isinstance(tick.volume, (int, float)) or not math.isfinite(tick.volume) or tick.volume < 0:
            raise InvalidTickError(f'Tick volume {tick.volume!r} must be finite and non-negative.')

    def _new_bucket(self, start: datetime, tick: Tick) -> _Bucket:
        return _Bucket(start, tick.price, tick.price, tick.price, tick.price, float(tick.volume))

    def _note_gap(self, prev_start: datetime, new_start: datetime) -> None:
        expected = prev_start + self._delta
        if prev_start.date() == new_start.date() and new_start > expected:
            self.gaps.append((expected, new_start))
            logger.warning('Candle gap for %s: %s to %s (not filled).', self.symbol, expected, new_start)

    def _close_bucket(self) -> Candle:
        b = self._bucket
        assert b is not None
        candle = validate_candle(
            symbol=self.symbol,
            timestamp=b.start,
            open_price=b.open,
            high_price=b.high,
            low_price=b.low,
            close_price=b.close,
            volume=b.volume,
            timeframe_minutes=self.timeframe,
        )
        self._last_closed_start = b.start
        self._bucket = None
        return candle

    def add_tick(self, tick: Tick) -> Optional[Candle]:
        '''Add a tick. Returns a completed candle when this tick opens a newer bucket.'''
        self._validate(tick)
        if not is_market_open(tick.timestamp):
            self.off_session_ticks_dropped += 1
            return None
        start = bucket_start(tick.timestamp, self.timeframe)

        if self._bucket is None:
            if self._last_closed_start is not None:
                if start <= self._last_closed_start:
                    self.late_ticks_dropped += 1
                    return None
                self._note_gap(self._last_closed_start, start)
            self._bucket = self._new_bucket(start, tick)
            return None

        if start < self._bucket.start:
            self.late_ticks_dropped += 1
            return None
        if start == self._bucket.start:
            b = self._bucket
            b.high = max(b.high, tick.price)
            b.low = min(b.low, tick.price)
            b.close = tick.price
            b.volume += float(tick.volume)
            return None

        candle = self._close_bucket()
        self._note_gap(candle.timestamp, start)
        self._bucket = self._new_bucket(start, tick)
        return candle

    def close_if_complete(self, now: datetime) -> Optional[Candle]:
        '''Close the open bucket once wall-clock time passes its end (no new tick needed).'''
        if self._bucket is None:
            return None
        if to_ist(now) >= self._bucket.start + self._delta:
            return self._close_bucket()
        return None

    def get_status(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'open_bucket_start': self._bucket.start.isoformat() if self._bucket else None,
            'gaps': [(a.isoformat(), b.isoformat()) for a, b in self.gaps],
            'late_ticks_dropped': self.late_ticks_dropped,
            'off_session_ticks_dropped': self.off_session_ticks_dropped,
        }
