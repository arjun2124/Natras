from datetime import datetime

import pytest

from natras.data.session import IST, bucket_start, is_market_open


def at(day, h, m, s=0):
    return datetime(2026, 10, day, h, m, s, tzinfo=IST)


def test_open_and_close_boundaries():
    assert not is_market_open(at(14, 9, 14, 59))
    assert is_market_open(at(14, 9, 15))
    assert is_market_open(at(14, 15, 29, 59))
    assert not is_market_open(at(14, 15, 30))


def test_weekend_closed():
    assert not is_market_open(at(17, 10, 0))


def test_naive_datetime_refused():
    with pytest.raises(ValueError):
        is_market_open(datetime(2026, 10, 14, 10, 0))


def test_bucket_start():
    assert bucket_start(at(14, 9, 17, 45)) == at(14, 9, 15)
