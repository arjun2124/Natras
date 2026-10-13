from datetime import date, datetime

from natras.options.symbol_master import IST, parse_nifty_options

REAL_ROWS = [
    "101126101335028,NIFTY 13 Oct 26 17350 CE,14,65,0.05,,0915-1540|1815-1915:,2026-10-12,1791886200,NSE:NIFTY26O1317350CE,10,11,35028,NIFTY,26000,17350.0,CE,101000000026000,None,0,0.0",
    "101126101335029,NIFTY 13 Oct 26 17350 PE,14,65,0.05,,0915-1540|1815-1915:,2026-10-12,1791886200,NSE:NIFTY26O1317350PE,10,11,35029,NIFTY,26000,17350.0,PE,101000000026000,None,0,0.0",
    "101126101335034,NIFTY 13 Oct 26 17400 CE,14,65,0.05,,0915-1540|1815-1915:,2026-10-12,1791886200,NSE:NIFTY26O1317400CE,10,11,35034,NIFTY,26000,17400.0,CE,101000000026000,None,0,0.0",
    "101126101335035,NIFTY 13 Oct 26 17400 PE,14,65,0.05,,0915-1540|1815-1915:,2026-10-12,1791886200,NSE:NIFTY26O1317400PE,10,11,35035,NIFTY,26000,17400.0,PE,101000000026000,None,0,0.0",
    "101126101335292,NIFTY 13 Oct 26 17150 CE,14,65,0.05,,0915-1540|1815-1915:,2026-10-12,1791886200,NSE:NIFTY26O1317150CE,10,11,35292,NIFTY,26000,17150.0,CE,101000000026000,None,0,0.0",
    "101126101335293,NIFTY 13 Oct 26 17150 PE,14,65,0.05,,0915-1540|1815-1915:,2026-10-12,1791886200,NSE:NIFTY26O1317150PE,10,11,35293,NIFTY,26000,17150.0,PE,101000000026000,None,0,0.0",
]

REAL_NON_OPTIONS = [
    "101126102748699,BANKNIFTY 27 Oct 26 FUT,11,30,0.2,,0915-1540|1815-1915:,2026-10-12,1793095800,NSE:BANKNIFTY26OCTFUT,10,11,48699,BANKNIFTY,26009,-1.0,XX,101000000026009,None,0,0.0",
    "101126102748704,NIFTY 27 Oct 26 FUT,11,65,0.1,,0915-1540|1815-1915:,2026-10-12,1793095800,NSE:NIFTY26OCTFUT,10,11,48704,NIFTY,26000,-1.0,XX,101000000026000,None,0,0.0",
]


def make_row(strike, option_type, expiry=date(2026, 10, 13), lot=65,
             underlying="NIFTY", instrument_type="14"):
    epoch = int(datetime(expiry.year, expiry.month, expiry.day, 15, 40, tzinfo=IST).timestamp())
    ticker = f"NSE:{underlying}26O{expiry:%d}{int(strike)}{option_type}"
    cols = [
        "1", f"{underlying} {int(strike)} {option_type}", instrument_type, str(lot),
        "0.05", "", "0915-1540|1815-1915:", "2026-10-12", str(epoch), ticker,
        "10", "11", "1", underlying, "26000", f"{float(strike)}", option_type,
        "101000000026000", "None", "0", "0.0",
    ]
    return ",".join(cols)


def test_real_rows_parse():
    contracts, stats = parse_nifty_options(REAL_ROWS)
    assert len(contracts) == 6
    assert stats.malformed_rows == 0
    first = contracts[0]
    assert first.symbol == "NSE:NIFTY26O1317350CE"
    assert first.strike == 17350.0
    assert first.option_type == "CE"
    assert first.lot_size == 65
    assert first.tick_size == 0.05
    assert first.expiry_date == date(2026, 10, 13)


def test_futures_and_other_underlyings_excluded():
    contracts, stats = parse_nifty_options(REAL_NON_OPTIONS)
    assert contracts == []
    assert stats.nifty_option_rows == 0
    assert stats.rows_total == 2


def test_other_index_options_excluded():
    rows = [make_row(25000, "CE", underlying="BANKNIFTY"), make_row(25000, "CE")]
    contracts, _ = parse_nifty_options(rows)
    assert len(contracts) == 1
    assert contracts[0].symbol.startswith("NSE:NIFTY")


def test_malformed_row_counted_and_skipped():
    bad = make_row(25000, "CE").replace("25000.0", "abc")
    contracts, stats = parse_nifty_options([bad, make_row(25050, "CE")])
    assert len(contracts) == 1
    assert stats.malformed_rows == 1
    assert stats.nifty_option_rows == 2


def test_short_and_blank_rows_skipped():
    contracts, stats = parse_nifty_options(["", "a,b,c", make_row(25000, "PE")])
    assert len(contracts) == 1
    assert stats.rows_total == 2 or stats.rows_total == 3
