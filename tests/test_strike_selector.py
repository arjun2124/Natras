from datetime import date, datetime

import pytest

from natras.options.strike_selector import (
    Moneyness,
    OptionChain,
    StrikePolicy,
    StrikeSelectionError,
    resolve_policy,
    select_contract,
)
from natras.options.symbol_master import parse_nifty_options
from tests.test_symbol_master import make_row

EXP1 = date(2026, 10, 13)
EXP2 = date(2026, 10, 20)
STRIKES = [24900, 24950, 25000, 25050, 25100, 25150, 25200]


def build_chain(lot=65):
    rows = []
    for expiry in (EXP1, EXP2):
        for strike in STRIKES:
            for opt in ("CE", "PE"):
                rows.append(make_row(strike, opt, expiry=expiry, lot=lot))
    contracts, _ = parse_nifty_options(rows)
    return OptionChain(contracts)


def sel(chain, spot, opt, label, today=EXP1, **kw):
    return select_contract(
        chain, spot=spot, option_type=opt,
        moneyness=Moneyness.parse(label), today=today, **kw,
    )


def test_nearest_expiry_includes_today_by_default():
    chain = build_chain()
    assert chain.nearest_expiry(EXP1) == EXP1
    assert chain.nearest_expiry(EXP1, min_days_to_expiry=1) == EXP2
    assert chain.nearest_expiry(date(2026, 10, 10)) == EXP1


def test_no_expiry_available_raises():
    chain = build_chain()
    with pytest.raises(StrikeSelectionError):
        chain.nearest_expiry(date(2026, 11, 1))


def test_atm_picks_closest_strike():
    chain = build_chain()
    s = sel(chain, 25012.0, "CE", "ATM")
    assert s.contract.strike == 25000.0
    assert s.atm_strike == 25000.0
    assert s.expiry == EXP1
    assert s.lot_size == 65


def test_tie_goes_to_lower_strike():
    chain = build_chain()
    assert sel(chain, 25025.0, "CE", "ATM").contract.strike == 25000.0


@pytest.mark.parametrize(
    "opt,label,expected",
    [
        ("CE", "ITM1", 24950.0),
        ("CE", "OTM1", 25050.0),
        ("CE", "ITM2", 24900.0),
        ("CE", "OTM2", 25100.0),
        ("PE", "ITM1", 25050.0),
        ("PE", "OTM1", 24950.0),
        ("PE", "ITM2", 25100.0),
        ("PE", "OTM2", 24900.0),
    ],
)
def test_moneyness_direction(opt, label, expected):
    chain = build_chain()
    assert sel(chain, 25000.0, opt, label).contract.strike == expected


def test_selected_symbol_is_verbatim_from_master():
    chain = build_chain()
    s = sel(chain, 25000.0, "PE", "ATM")
    assert s.contract.symbol == "NSE:NIFTY26O1325000PE"


def test_outside_listed_strikes_raises():
    chain = build_chain()
    with pytest.raises(StrikeSelectionError):
        sel(chain, 24900.0, "CE", "ITM2")
    with pytest.raises(StrikeSelectionError):
        sel(chain, 25200.0, "CE", "OTM1")


def test_spot_far_from_chain_raises():
    chain = build_chain()
    with pytest.raises(StrikeSelectionError):
        sel(chain, 26000.0, "CE", "ATM")
    assert sel(chain, 26000.0, "CE", "ATM", max_atm_gap=2000.0).contract.strike == 25200.0


@pytest.mark.parametrize("spot", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_spot_raises(spot):
    chain = build_chain()
    with pytest.raises(StrikeSelectionError):
        sel(chain, spot, "CE", "ATM")


def test_invalid_option_type_raises():
    chain = build_chain()
    with pytest.raises(StrikeSelectionError):
        sel(chain, 25000.0, "XX", "ATM")


@pytest.mark.parametrize("text", ["ITM0", "OTM0", "FOO", "", "ITM", "ATM1"])
def test_bad_moneyness_raises(text):
    with pytest.raises(StrikeSelectionError):
        Moneyness.parse(text)


def test_moneyness_parse_case_insensitive():
    assert Moneyness.parse(" itm2 ") == Moneyness("ITM", 2)
    assert Moneyness.parse("atm").label == "ATM"
    assert Moneyness("OTM", 3).label == "OTM3"


def test_inconsistent_lot_sizes_raise():
    rows = [make_row(25000, "CE", lot=65), make_row(25050, "CE", lot=75)]
    contracts, _ = parse_nifty_options(rows)
    chain = OptionChain(contracts)
    with pytest.raises(StrikeSelectionError):
        sel(chain, 25000.0, "CE", "ATM")


def test_master_lot_size_wins_over_hint():
    chain = build_chain(lot=75)
    assert sel(chain, 25000.0, "CE", "ATM").lot_size == 75


def test_conflicting_duplicate_contract_raises():
    contracts, _ = parse_nifty_options([make_row(25000, "CE")])
    other = contracts[0].__class__(
        **{**contracts[0].__dict__, "symbol": "NSE:NIFTY26O1325000CE".replace("26O", "26X")}
    )
    with pytest.raises(StrikeSelectionError):
        OptionChain([contracts[0], other])


def test_identical_duplicate_is_deduplicated():
    contracts, _ = parse_nifty_options([make_row(25000, "CE")] * 2)
    chain = OptionChain(contracts)
    assert chain.strikes(EXP1, "CE") == [25000.0]


def test_policy_default_live_atm_with_shadows():
    chain = build_chain()
    result = resolve_policy(
        chain, StrikePolicy(), spot=25000.0, option_type="CE", today=EXP1
    )
    assert result.live.moneyness.label == "ATM"
    assert result.live.contract.strike == 25000.0
    assert result.shadows["ITM1"].contract.strike == 24950.0
    assert result.shadows["OTM1"].contract.strike == 25050.0
    assert result.shadow_errors == {}


def test_policy_shadow_failure_recorded_not_raised():
    chain = build_chain()
    result = resolve_policy(
        chain, StrikePolicy(), spot=25200.0, option_type="CE", today=EXP1
    )
    assert result.live.contract.strike == 25200.0
    assert "OTM1" in result.shadow_errors
    assert "ITM1" in result.shadows


def test_policy_live_failure_propagates():
    chain = build_chain()
    with pytest.raises(StrikeSelectionError):
        resolve_policy(
            chain, StrikePolicy(), spot=30000.0, option_type="CE", today=EXP1
        )


def test_custom_live_moneyness():
    chain = build_chain()
    policy = StrikePolicy(live=Moneyness("ITM", 1), shadows=())
    result = resolve_policy(
        chain, policy, spot=25000.0, option_type="PE", today=EXP1
    )
    assert result.live.contract.strike == 25050.0
    assert result.shadows == {}
