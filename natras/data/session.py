'''NSE session helpers. All times are Asia/Kolkata; naive datetimes are refused.'''

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from natras.config.constants import CANDLE_TIMEFRAME_MINUTES, MARKET_TIMEZONE_STR

IST = ZoneInfo(MARKET_TIMEZONE_STR)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def to_ist(ts: datetime) -> datetime:
    '''Convert an aware datetime to IST. Never guess the zone of a naive one.'''
    if ts.tzinfo is None:
        raise ValueError('Timestamp must be timezone-aware; refusing to guess a timezone.')
    return ts.astimezone(IST)


def is_market_open(ts: datetime) -> bool:
    '''True on weekdays from 09:15:00 up to (excluding) 15:30:00 IST.

    Exchange holidays are NOT modelled here; ticks only arrive on trading days.
    '''
    local = to_ist(ts)
    if local.weekday() >= 5:
        return False
    return MARKET_OPEN <= local.time() < MARKET_CLOSE


def bucket_start(ts: datetime, timeframe_minutes: int = CANDLE_TIMEFRAME_MINUTES) -> datetime:
    '''Start of the candle bucket (IST) that contains ts.'''
    local = to_ist(ts)
    minute = local.minute - (local.minute % timeframe_minutes)
    return local.replace(minute=minute, second=0, microsecond=0)
