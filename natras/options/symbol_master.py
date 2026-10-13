"""Parser for the public FYERS NSE F&O symbol master (NIFTY index options only).

Column layout (0-indexed, no header row), confirmed against the real file on
2026-10-14:

     0 fytoken            8 expiry (epoch seconds)
     1 description        9 FYERS ticker, e.g. NSE:NIFTY26O1317350CE
     2 instrument type   13 underlying symbol, e.g. NIFTY
       (14 = option)     15 strike price
     3 lot size          16 option type (CE / PE)
     4 tick size

Symbols are always taken verbatim from column 9. They are never constructed.
"""

from __future__ import annotations

import csv
import logging
import math
import os
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
SYMBOL_MASTER_URL = "https://public.fyers.in/sym_details/NSE_FO.csv"
DEFAULT_MASTER_PATH = Path("data") / "symbol_master" / "NSE_FO.csv"

COL_FYTOKEN = 0
COL_DESCRIPTION = 1
COL_INSTRUMENT_TYPE = 2
COL_LOT_SIZE = 3
COL_TICK_SIZE = 4
COL_EXPIRY_EPOCH = 8
COL_SYMBOL = 9
COL_UNDERLYING = 13
COL_STRIKE = 15
COL_OPTION_TYPE = 16
MIN_COLUMNS = 17

OPTION_INSTRUMENT_TYPE = "14"
UNDERLYING = "NIFTY"

# Informational only: used for a log warning, never to override the master.
NIFTY_LOT_SIZE_HINT = 65


@dataclass(frozen=True)
class OptionContract:
    fytoken: str
    description: str
    symbol: str
    expiry_epoch: int
    expiry_date: date
    strike: float
    option_type: str
    lot_size: int
    tick_size: float


@dataclass(frozen=True)
class ParseStats:
    rows_total: int
    nifty_option_rows: int
    malformed_rows: int


def _build_contract(row: list[str], option_type: str) -> OptionContract:
    lot_size = int(row[COL_LOT_SIZE])
    tick_size = float(row[COL_TICK_SIZE])
    expiry_epoch = int(row[COL_EXPIRY_EPOCH])
    strike = float(row[COL_STRIKE])
    symbol = row[COL_SYMBOL].strip()
    if lot_size <= 0 or tick_size <= 0:
        raise ValueError("non-positive lot or tick size")
    if not math.isfinite(strike) or strike <= 0:
        raise ValueError("invalid strike")
    if not symbol.startswith("NSE:NIFTY") or not symbol.endswith(option_type):
        raise ValueError("unexpected symbol format")
    expiry_date = datetime.fromtimestamp(expiry_epoch, tz=IST).date()
    return OptionContract(
        fytoken=row[COL_FYTOKEN].strip(),
        description=row[COL_DESCRIPTION].strip(),
        symbol=symbol,
        expiry_epoch=expiry_epoch,
        expiry_date=expiry_date,
        strike=strike,
        option_type=option_type,
        lot_size=lot_size,
        tick_size=tick_size,
    )


def parse_nifty_options(
    lines: Iterable[str],
) -> tuple[list[OptionContract], ParseStats]:
    """Parse symbol master lines, keeping only NIFTY index CE/PE contracts.

    The underlying column must equal exactly ``NIFTY`` so BANKNIFTY, FINNIFTY,
    MIDCPNIFTY and NIFTYNXT50 are excluded. Futures are excluded by the
    instrument type column. Rows that look like NIFTY options but fail
    validation are counted as malformed and skipped.
    """
    contracts: list[OptionContract] = []
    total = matched = malformed = 0
    for row in csv.reader(lines):
        total += 1
        if len(row) < MIN_COLUMNS:
            continue
        if row[COL_UNDERLYING].strip() != UNDERLYING:
            continue
        if row[COL_INSTRUMENT_TYPE].strip() != OPTION_INSTRUMENT_TYPE:
            continue
        option_type = row[COL_OPTION_TYPE].strip().upper()
        if option_type not in ("CE", "PE"):
            continue
        matched += 1
        try:
            contracts.append(_build_contract(row, option_type))
        except (ValueError, OverflowError, OSError):
            malformed += 1
    stats = ParseStats(
        rows_total=total, nifty_option_rows=matched, malformed_rows=malformed
    )
    return contracts, stats


def load_nifty_options(
    path: str | os.PathLike[str] = DEFAULT_MASTER_PATH,
) -> tuple[list[OptionContract], ParseStats]:
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        return parse_nifty_options(handle)


def download_symbol_master(
    dest: str | os.PathLike[str] = DEFAULT_MASTER_PATH,
    url: str = SYMBOL_MASTER_URL,
    timeout: float = 60.0,
) -> Path:
    """Download the public symbol master atomically. Not covered by unit tests."""
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest_path.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(
            url, timeout=timeout
        ) as resp:
            size = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                size += len(chunk)
        if size == 0:
            raise OSError("downloaded symbol master is empty")
        os.replace(tmp_name, dest_path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    return dest_path
