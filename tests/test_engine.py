import queue

import pytest

from quantdesk.broker.base import Position
from quantdesk.broker.dry_run import DryRunBroker
from quantdesk.engine import ENTRY_ORDER_TIMEOUT, Engine
from quantdesk.strategy import CANCELLED, FILLED, OFF, ON, PLACED, ValidationError
from tests.fakes import FakeBroker, FakeMarketData


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def data_file(tmp_path):
    return str(tmp_path / "equities.json")


def make_engine(broker, data_file, clock=None):
    return Engine(broker, FakeMarketData(), data_file, order_qty=1, events=queue.Queue(), clock=clock or Clock())


def events_text(engine):
    out = []
    while not engine.events.empty():
        out.append(engine.events.get().message)
    return "\n".join(out)


def aapl_on(engine, levels=3, drawdown=0.02):
    engine.add_system("AAPL", levels, drawdown)
    engine.toggle(["AAPL"])


def test_entry_market_buy_when_no_position(data_file):
    b = FakeBroker()
    e = make_engine(b, data_file)
    aapl_on(e)
    e.run_once()
    assert b.placed == [("MKT", "AAPL", "BUY", 1, None)]
    s = e.systems["AAPL"]
    assert s.entry_order_id == "O1" and s.entry_price is None and s.levels == []


def test_no_double_entry_while_entry_order_open(data_file):
    b = FakeBroker()
    e = make_engine(b, data_file)
    aapl_on(e)
    for _ in range(5):
        e.run_once()
    assert len(b.orders_of_type("MKT")) == 1


def test_entry_price_set_once_and_limit_orders_per_level(data_file):
    b = FakeBroker()
    e = make_engine(b, data_file)
    aapl_on(e, levels=3, drawdown=0.02)
    e.run_once()
    b.fill("O1", 200.0)
    e.run_once()
    s = e.systems["AAPL"]
    assert s.entry_price == 200.0 and s.position == 1
    assert [o[4] for o in b.placed if o[0] == "LMT"] == [196.0, 192.0, 188.0]
    assert all(lv.status == PLACED and lv.order_id for lv in s.levels)
    # Preis der Position ändert sich (Nachkauf) – Einstiegspreis bleibt
    b.positions["AAPL"] = Position("AAPL", 2, 198.0)
    e.run_once()
    assert e.systems["AAPL"].entry_price == 200.0


def test_entry_price_is_fill_price_without_commission(data_file):
    # Live-Test 29.09.2026: Kauf zu 331.21, IBKR-Position meldet 332.21 (inkl. ~1 USD Kommission)
    b = FakeBroker()
    e = make_engine(b, data_file)
    aapl_on(e, levels=1, drawdown=0.02)
    e.run_once()
    b.fill("O1", 331.21, commission=1.0)
    assert b.positions["AAPL"].avg_price == 332.21
    e.run_once()
    assert e.systems["AAPL"].entry_price == 331.21
    assert [o[4] for o in b.placed if o[0] == "LMT"] == [324.59]


def test_each_level_placed_exactly_once(data_file):
    b = FakeBroker()
    e = make_engine(b, data_file)
    aapl_on(e, levels=5, drawdown=0.01)
    e.run_once()
    b.fill("O1", 100.0)
    for _ in range(20):
        e.run_once()
    assert len(b.orders_of_type("LMT")) == 5
    assert len(e.systems["AAPL"].levels) == 5


def test_filled_and_cancelled_levels_detected(data_file):
    b = FakeBroker()
    e = make_engine(b, data_file)
    aapl_on(e, levels=2, drawdown=0.02)
    e.run_once()
    b.fill("O1", 100.0)
    e.run_once()
    lv1, lv2 = e.systems["AAPL"].levels
    b.fill(lv1.order_id, lv1.price)
    b.cancel_order(lv2.order_id)
    e.run_once()
    assert (lv1.status, lv2.status) == (FILLED, CANCELLED)
    assert len(b.orders_of_type("LMT")) == 2  # storniertes Level wird nicht neu platziert
    assert "gefüllt" in events_text(e)


def test_foreign_position_is_never_adopted(data_file):
    # Fall LLY: Tim hält schon 183 Stück. Der Bot kauft seine eigenen und verkauft nur seine eigenen.
    b = FakeBroker()
    b.positions["AAPL"] = Position("AAPL", 183, 150.0)
    e = make_engine(b, data_file)
    aapl_on(e, levels=2, drawdown=0.1)
    e.run_once()
    assert b.placed == [("MKT", "AAPL", "BUY", 1, None)]  # eigener Einstieg trotz fremder Position
    b.fill("O1", 100.0)
    e.run_once()
    s = e.systems["AAPL"]
    assert s.entry_price == 100.0 and s.bot_qty() == 1  # eigener Fill zählt, nicht der Durchschnitt aller 184
    assert [o[4] for o in b.placed[1:]] == [90.0, 80.0]
    assert any("fremde" in m for m in events_text(e).splitlines())


def test_off_system_does_nothing(data_file):
    b = FakeBroker()
    e = make_engine(b, data_file)
    e.add_system("AAPL", 3, 0.02)
    for _ in range(3):
        e.run_once()
    assert b.placed == []


def test_read_error_never_buys(data_file):
    # Bug im Original: jede Exception bei get_position -> neuer Market-Buy
    b = FakeBroker()
    b.fail_reads = True
    e = make_engine(b, data_file)
    aapl_on(e)
    for _ in range(3):
        e.run_once()
    assert b.placed == []
    assert "Runde übersprungen" in events_text(e)


def test_not_authenticated_does_nothing(data_file):
    b = FakeBroker()
    b.authenticated = False
    e = make_engine(b, data_file)
    aapl_on(e)
    e.run_once()
    assert b.placed == []
    assert "localhost:5000" in events_text(e)


def test_stop_all(data_file):
    b = FakeBroker()
    e = make_engine(b, data_file)
    aapl_on(e)
    e.add_system("MSFT", 2, 0.02)
    e.toggle(["MSFT"])
    e.stop_all()
    assert e.paused and all(s.status == OFF for s in e.systems.values())
    e.toggle(["AAPL"])  # trotzdem einschalten -> Engine bleibt pausiert
    e.run_once()
    assert b.placed == []
    e.resume()
    e.run_once()
    assert b.placed == [("MKT", "AAPL", "BUY", 1, None)]


def test_dry_run_sends_nothing(data_file):
    b = FakeBroker()
    dry = DryRunBroker(b, price_fn=lambda s: 50.0)
    e = make_engine(dry, data_file)
    aapl_on(e, levels=3, drawdown=0.1)
    e.run_once()  # simulierter Einstieg
    e.run_once()  # Einstiegspreis + simulierte Limits
    e.run_once()
    assert b.placed == [] and b.cancelled == []
    s = e.systems["AAPL"]
    assert s.entry_price == 50.0 and s.simulated
    assert [lv.price for lv in s.levels] == [45.0, 40.0, 35.0]
    assert all(lv.order_id.startswith("DRY-") for lv in s.levels)
    e.remove("AAPL", cancel_orders=True)
    assert b.cancelled == []


def test_simulated_state_dropped_on_restart(data_file):
    dry = DryRunBroker(FakeBroker(), price_fn=lambda s: 50.0)
    e = make_engine(dry, data_file)
    aapl_on(e)
    e.run_once()
    e.run_once()
    assert e.systems["AAPL"].levels
    e2 = make_engine(FakeBroker(), data_file)
    s = e2.systems["AAPL"]
    assert s.entry_price is None and s.levels == [] and s.status == ON


def test_persistence_roundtrip_no_duplicate_orders(data_file):
    b = FakeBroker()
    e = make_engine(b, data_file)
    aapl_on(e, levels=3, drawdown=0.02)
    e.run_once()
    b.fill("O1", 100.0)
    e.run_once()
    before = len(b.placed)
    e2 = make_engine(b, data_file)  # Neustart: aus equities.json geladen
    for _ in range(3):
        e2.run_once()
    assert len(b.placed) == before
    assert e2.systems["AAPL"].entry_price == 100.0


def test_foreign_open_order_at_level_price_is_not_adopted(data_file):
    b = FakeBroker()
    b.place_limit_order("AAPL", "BUY", 1, 98.0)  # Tims eigene Order, zufällig auf Level-Preis
    b.placed.clear()
    e = make_engine(b, data_file)
    aapl_on(e, levels=2, drawdown=0.02)
    e.run_once()
    b.fill("O2", 100.0)
    e.run_once()
    assert [o[4] for o in b.placed if o[0] == "LMT"] == [98.0, 96.0]  # eigene Level-Orders
    assert e.systems["AAPL"].levels[0].order_id != "O1"


def test_cancelled_entry_order_is_retried(data_file):
    b = FakeBroker()
    e = make_engine(b, data_file)
    aapl_on(e)
    e.run_once()
    b.cancel_order("O1")
    e.run_once()  # erkennt Storno
    e.run_once()  # neuer Einstieg
    assert len(b.orders_of_type("MKT")) == 2


def test_vanished_entry_order_retried_only_after_timeout(data_file):
    b = FakeBroker()
    clock = Clock()
    e = make_engine(b, data_file, clock)
    aapl_on(e)
    e.run_once()
    del b.orders["O1"]
    e.run_once()
    assert len(b.placed) == 1
    clock.t += ENTRY_ORDER_TIMEOUT + 1
    e.run_once()
    e.run_once()
    assert len(b.placed) == 2


def test_foreign_open_market_buy_does_not_block_entry(data_file):
    b = FakeBroker()
    b.place_market_order("AAPL", "BUY", 1)  # fremde Order
    b.placed.clear()
    e = make_engine(b, data_file)
    aapl_on(e)
    e.run_once()
    assert b.placed == [("MKT", "AAPL", "BUY", 1, None)]


def test_order_error_keeps_level_pending_and_bot_runs(data_file):
    b = FakeBroker()
    e = make_engine(b, data_file)
    aapl_on(e, levels=2, drawdown=0.02)
    e.run_once()
    b.fill("O1", 100.0)
    b.fail_orders = True
    e.run_once()
    assert [lv.status for lv in e.systems["AAPL"].levels] == ["pending", "pending"]
    b.fail_orders = False
    e.run_once()
    assert len(b.orders_of_type("LMT")) == 2


def test_remove_cancels_open_orders(data_file):
    b = FakeBroker()
    e = make_engine(b, data_file)
    aapl_on(e, levels=2, drawdown=0.02)
    e.run_once()
    b.fill("O1", 100.0)
    e.run_once()
    assert e.view[0]["open_orders"] == 2
    e.remove("AAPL", cancel_orders=True)
    assert sorted(b.cancelled) == ["O2", "O3"]
    assert "AAPL" not in e.systems


def test_duplicate_symbol_rejected(data_file):
    e = make_engine(FakeBroker(), data_file)
    e.add_system("AAPL", 3, 0.02)
    with pytest.raises(ValidationError):
        e.add_system("AAPL", 2, 0.01)


def test_corrupt_file_is_backed_up(data_file, tmp_path):
    with open(data_file, "w") as f:
        f.write("{kaputt")
    e = make_engine(FakeBroker(), data_file)
    assert e.systems == {}
    assert "ungültig" in events_text(e)
    assert list(tmp_path.glob("*.corrupt.json"))
