import json

import pytest

from quantdesk.broker.base import Order
from quantdesk.strategy import (
    MAX_LEVELS,
    PLACED,
    EquitySystem,
    Level,
    ValidationError,
    build_levels,
    find_level_order,
    level_price,
    parse_grid_input,
    validate_grid,
)


def test_level_prices_match_video_formula():
    # Werte aus equities.json im Referenz-Repo (AAPL, Einstieg 213.25, 5 %)
    prices = [lv.price for lv in build_levels(213.25, 5, 0.05)]
    assert prices == [202.59, 191.93, 181.26, 170.6, 159.94]
    assert level_price(100, 0.02, 3) == 94.0


@pytest.mark.parametrize(
    "levels, drawdown",
    [(0, 0.02), (MAX_LEVELS + 1, 0.01), (3, 0), (3, -0.02), (20, 0.05), (10, 0.1), (2, 0.6)],
)
def test_invalid_grids_rejected(levels, drawdown):
    with pytest.raises(ValidationError):
        validate_grid(levels, drawdown)


def test_no_negative_or_zero_prices_possible():
    # Bug im Original: bis −362 im JSON. Jetzt: jede gültige Konfiguration hat nur Preise > 0
    for n in range(1, MAX_LEVELS + 1):
        dd = 0.999 / n
        assert all(lv.price > 0 for lv in build_levels(10.0, n, dd))


def test_parse_grid_input():
    assert parse_grid_input(" aapl ", "3", "2,5") == ("AAPL", 3, 0.025)
    assert parse_grid_input("BRK.B", "1", "1") == ("BRK.B", 1, 0.01)
    for bad in [("", "3", "2"), ("AAPL", "x", "2"), ("AAPL", "3", "abc"), ("AAPL", "3", "40"), ("1ABC", "3", "2")]:
        with pytest.raises(ValidationError):
            parse_grid_input(*bad)


def test_levels_do_not_grow_over_many_cycles():
    s = EquitySystem("AAPL", 3, 0.02, entry_price=100.0)
    for _ in range(50):
        s.ensure_levels()
    assert [lv.level for lv in s.levels] == [1, 2, 3]


def test_json_roundtrip_keeps_int_levels_and_order_ids():
    s = EquitySystem("AAPL", 3, 0.02, entry_price=100.0, status="On")
    s.ensure_levels()
    s.levels[0].status, s.levels[0].order_id = PLACED, "123"
    restored = EquitySystem.from_dict(json.loads(json.dumps(s.to_dict())))
    assert restored == s
    assert all(isinstance(lv.level, int) for lv in restored.levels)
    # Nach dem Laden wird das platzierte Level wiedererkannt -> keine Doppel-Order
    assert [lv.level for lv in restored.pending_levels()] == [2, 3]


def test_legacy_dict_levels_rejected():
    legacy = {"symbol": "AAPL", "num_levels": 3, "drawdown": 0.05, "levels": {"1": 202.59, "-1": 202.59}}
    with pytest.raises(ValueError):
        EquitySystem.from_dict(legacy)


def _order(oid, price, status="Submitted", symbol="AAPL", side="BUY"):
    return Order(oid, symbol, side, 1, "LMT", price, status)


def test_find_level_order_by_id_and_tolerance():
    lv = Level(1, 202.59)
    assert find_level_order(lv, "AAPL", [_order("a", 202.595)]).order_id == "a"
    assert find_level_order(lv, "AAPL", [_order("a", 202.60)]).order_id == "a"
    assert find_level_order(lv, "AAPL", [_order("a", 202.62)]) is None
    assert find_level_order(lv, "AAPL", [_order("a", 202.59, symbol="MSFT")]) is None
    assert find_level_order(lv, "AAPL", [_order("a", 202.59, side="SELL")]) is None
    # per Preis nur offene Orders, per ID auch gefüllte
    assert find_level_order(lv, "AAPL", [_order("a", 202.59, "Filled")]) is None
    lv.order_id = "a"
    assert find_level_order(lv, "AAPL", [_order("a", 202.59, "Filled")]).is_filled
