"""Fills von Orders aus früheren Sitzungen: IBKR listet sie nicht mehr unter /iserver/account/orders,
sondern nur noch unter /iserver/account/trades (7 Tage). Nachgestellt nach dem Fall KO vom 2.10.2026."""
import calendar
import json
import queue
import time

import pytest

import forward_test as cli
from quantdesk.broker.base import Execution, Order, Position
from quantdesk.broker.ibkr import parse_executions
from quantdesk.engine import EXEC_REFRESH_SECONDS, Engine
from quantdesk.executions import ExecutionArchive, aggregate
from quantdesk.history import Bar
from quantdesk.journal import Journal
from quantdesk.registry import BotOrder, OrderRegistry
from quantdesk.strategy import FILLED, PLACED
from tests.fakes import FakeBroker, FakeMarketData

OCT2 = calendar.timegm(time.strptime("2026-10-02 14:58:23", "%Y-%m-%d %H:%M:%S"))


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def ex(order_id, qty, price, ts=OCT2, side="BUY", symbol="KO", exec_id=None):
    return Execution(exec_id or f"x{order_id}-{qty}", str(order_id), symbol, side, qty, price, ts)


def engine(tmp_path, broker, trend=True, clock=None, bars=None):
    e = Engine(broker, FakeMarketData(87.0), str(tmp_path / "eq.json"), events=queue.Queue(),
               trend=lambda s, n: trend, journal=Journal(str(tmp_path / "j.jsonl"), clock=clock or time.time),
               executions=ExecutionArchive(str(tmp_path / "ex.jsonl")), bars=bars,
               registry=OrderRegistry(str(tmp_path / "reg.jsonl")),
               **({"clock": clock} if clock else {}))
    return e


def ko_after_restart(tmp_path, broker, **kw):
    """KO: Einstieg 11 Stück @ 87, 5 Levels platziert, dann Bot-Neustart in neuer IBKR-Sitzung."""
    e = engine(tmp_path, broker, **kw)
    e.add_system("KO", 5, 0.02, trend_sma=200, trend_exit=True, order_usd=1000)
    e.toggle(["KO"])
    e.run_once()
    broker.fill("O1", 87.0)
    e.run_once()
    ids = [lv.order_id for lv in e.systems["KO"].levels]
    assert len(ids) == 5
    # Neue Sitzung: IBKR listet die alten Orders nicht mehr; Level 1 wurde inzwischen gefüllt
    broker.orders.clear()
    broker.positions["KO"] = Position("KO", 23, 86.1)
    return e, ids


# ------------------------------------------------------------------ IBKR-Antwort
def test_parse_ibkr_trades_response():
    raw = [
        {"execution_id": "00025b45.6ac1e740.01.01", "order_id": 410323124, "symbol": "KO", "side": "B",
         "size": 12.0, "price": "85.26", "trade_time": "20261002-14:58:23", "trade_time_r": 1790953103000,
         "account": "DUO844164"},
        {"execution_id": "y", "order_id": 1, "symbol": "X", "side": "S", "size": 1, "price": "1",
         "trade_time_r": 1, "account": "DU0000001"},  # anderes Konto
        {"execution_id": "z", "order_id": None, "symbol": "X", "side": "B", "size": 1, "price": "1",
         "trade_time_r": 1, "account": "DUO844164"},  # ohne Order-ID
    ]
    [e] = parse_executions(raw, "DUO844164")
    assert (e.order_id, e.symbol, e.side, e.qty, e.price) == ("410323124", "KO", "BUY", 12.0, 85.26)
    assert time.strftime("%Y-%m-%d %H:%M", time.gmtime(e.ts)) == "2026-10-02 14:58"


def test_ibkr_client_retries_empty_first_call():
    from tests.test_ibkr import ACCT, client, ok
    c = client({("GET", "/iserver/account/trades"): [ok([]), ok([
        {"execution_id": "e1", "order_id": 5, "symbol": "KO", "side": "B", "size": 2, "price": "10",
         "trade_time_r": 1_790_000_000_000, "account": ACCT}])]})
    assert [e.order_id for e in c.get_executions()] == ["5"]
    calls = [(m, p, k.get("params")) for m, p, k in c._session.calls if p == "/iserver/account/trades"]
    assert len(calls) == 2 and calls[0][2] == {"days": 7}


def test_archive_dedupes_and_aggregates_partial_fills(tmp_path):
    a = ExecutionArchive(str(tmp_path / "ex.jsonl"))
    a.merge([ex(7, 5, 10.0, exec_id="a"), ex(7, 7, 11.0, ts=OCT2 + 60, exec_id="b")])
    assert len(a.merge([ex(7, 5, 10.0, exec_id="a")])) == 2  # doppelt -> ignoriert
    f = aggregate(a.load())["7"]
    assert f.qty == 12 and f.price == pytest.approx((5 * 10 + 7 * 11) / 12) and f.ts == OCT2 + 60


# ------------------------------------------------------------------ Engine
def test_level_filled_in_earlier_session_is_booked_with_real_date(tmp_path):
    b = FakeBroker(price=87.0)
    e, ids = ko_after_restart(tmp_path, b)
    b.executions = [ex(ids[0], 12, 85.26)]
    e._last_exec_fetch = None  # wie nach einem Neustart
    e.run_once()
    lv = e.systems["KO"].levels[0]
    assert (lv.status, lv.qty) == (FILLED, 12)
    level_fills = [f for f in e.journal.read() if f.kind == "level"]
    assert [(f.day, f.qty, f.price, f.estimated) for f in level_fills] == [("2026-10-02", 12, 85.26, False)]
    e.run_once()
    assert len([f for f in e.journal.read() if f.kind == "level"]) == 1  # nicht doppelt gebucht
    assert all(lv.status == PLACED for lv in e.systems["KO"].levels[1:])


def test_executions_fetched_at_start_and_throttled(tmp_path):
    clock = Clock(OCT2 + 86400 * 5)
    b = FakeBroker(price=87.0)
    e, ids = ko_after_restart(tmp_path, b, clock=clock)
    e._last_exec_fetch = None  # Neustart
    calls = b.execution_calls
    e.run_once()
    e.run_once()
    assert b.execution_calls == calls + 1  # beim Start; danach trotz fehlender Orders höchstens alle 5 min
    clock.t += EXEC_REFRESH_SECONDS
    e.run_once()
    assert b.execution_calls == calls + 2


def test_fills_survive_the_7_day_window_via_archive(tmp_path):
    b = FakeBroker(price=87.0)
    e, ids = ko_after_restart(tmp_path, b)
    e.executions.merge([ex(ids[0], 12, 85.26)])  # früher gesehen / importiert
    b.executions = []  # IBKR liefert sie nicht mehr
    e._last_exec_fetch = None
    e.run_once()
    assert e.systems["KO"].levels[0].status == FILLED


def test_level_inferred_from_position_when_execution_is_gone(tmp_path):
    days = ["2026-09-29", "2026-09-30", "2026-10-01"]
    bars = [Bar(days[0], 87, 87.5, 86.5, 87), Bar(days[1], 86.8, 87, 85.0, 85.5), Bar(days[2], 85.5, 86, 85, 85.8)]
    b = FakeBroker(price=87.0)
    e, ids = ko_after_restart(tmp_path, b, bars=lambda s: bars)
    e.systems["KO"].entry_ts = calendar.timegm(time.strptime("2026-09-29", "%Y-%m-%d"))
    e.infer_missing = True  # nur in der einmaligen Migration erlaubt
    b.executions = []
    e._last_exec_fetch = None
    e.run_once()
    lv = e.systems["KO"].levels[0]
    assert (lv.status, lv.qty) == (FILLED, 12)
    f = [f for f in e.journal.read() if f.kind == "level"][0]
    assert (f.day, f.price, f.estimated) == ("2026-09-30", 85.26, True)  # erster Tag mit Tief ≤ 85.26


def test_no_inference_outside_migration_and_without_extra_shares(tmp_path):
    b = FakeBroker(price=87.0)
    e, ids = ko_after_restart(tmp_path, b)
    b.executions = []
    e._last_exec_fetch = None
    e.run_once()  # Position 23 > eigene 11, aber ausserhalb der Migration wird nichts abgeleitet
    assert all(lv.status == PLACED for lv in e.systems["KO"].levels)
    e.infer_missing = True
    b.positions["KO"] = Position("KO", 11, 87.0)  # keine zusätzlichen Stück -> nichts ableiten
    e.run_once()
    assert all(lv.status == PLACED for lv in e.systems["KO"].levels)
    assert [f.kind for f in e.journal.read()] == ["entry"]


def test_exit_from_earlier_session_is_booked(tmp_path):
    b = FakeBroker(price=87.0)
    e, ids = ko_after_restart(tmp_path, b)
    b.positions["KO"] = Position("KO", 11, 87.0)
    e.trend = lambda s, n: False
    e.run_once()  # Trendbruch: Levels storniert, Market-Sell
    exit_id = e.systems["KO"].exit_order_id
    assert exit_id
    b.orders.clear()  # neue Sitzung, Verkauf inzwischen ausgeführt
    del b.positions["KO"]
    b.executions = [ex(exit_id, 11, 84.0, side="SELL", ts=OCT2 + 86400)]
    e._last_exec_fetch = None
    e.run_once()
    s = e.systems["KO"]
    assert s.exit_order_id is None and s.entry_price is None
    assert [(f.side, f.kind, f.qty, f.price, f.day) for f in e.journal.read()][-1] == ("SELL", "exit", 11, 84.0, "2026-10-03")


def test_entry_filled_in_earlier_session_uses_execution(tmp_path):
    b = FakeBroker(price=87.0)
    e = engine(tmp_path, b)
    e.add_system("KO", 5, 0.02, trend_sma=200, trend_exit=True, order_usd=1000)
    e.toggle(["KO"])
    e.run_once()
    b.orders.clear()
    b.positions["KO"] = Position("KO", 11, 87.09)  # avgPrice inkl. Kommission
    b.executions = [ex("O1", 11, 87.0)]
    e._last_exec_fetch = None
    e.run_once()
    s = e.systems["KO"]
    assert (s.entry_price, s.entry_qty) == (87.0, 11)
    assert [(f.kind, f.price, f.day) for f in e.journal.read()] == [("entry", 87.0, "2026-10-02")]


def test_untracked_fill_of_own_order_is_booked_once_foreign_never(tmp_path):
    """Fall CVS: eigenes Level gefüllt, als der Bot aus war, danach Verkauf – das Level steht nicht mehr im Zustand.
    Ausführungen von Orders, die nicht im Register stehen (z.B. Tims Handkäufe), werden nie gebucht."""
    b = FakeBroker(price=87.0)
    e = engine(tmp_path, b)
    e.add_system("CVS", 5, 0.02, trend_sma=200, trend_exit=True, order_usd=1000)
    e.toggle(["CVS"])
    e.registry.add(BotOrder("410323215", "CVS", "level", "BUY", 12, 1, 85.26))
    b.executions = [ex("410323215", 12, 85.26, symbol="CVS"), ex("MANUELL", 50, 86.0, symbol="CVS")]
    e.trend = lambda s, n: None
    e.run_once()
    e._last_exec_fetch = None
    e.run_once()
    assert [(f.order_id, f.kind, f.exec_ids) for f in e.journal.read()] == [("410323215", "level", ("x410323215-12",))]


def test_missing_own_shares_raise_alarm_and_nothing_is_bought(tmp_path):
    b = FakeBroker(price=87.0)
    e, ids = ko_after_restart(tmp_path, b)
    del b.positions["KO"]  # z.B. Übernahme gegen Bargeld (Fall WBD)
    placed_before = len(b.placed)
    e.run_once()
    errors = []
    while not e.events.empty():
        ev = e.events.get()
        if ev.level == "error":
            errors.append(ev.message)
    assert any("Abweichung" in m for m in errors)
    assert len(b.placed) == placed_before and e.systems["KO"].entry_price == 87.0


def test_migration_fills_entry_qty_from_journal(tmp_path):
    f = tmp_path / "eq.json"
    j = Journal(str(tmp_path / "j.jsonl"))
    j.record("KO", "BUY", 11, 87.0, "entry", "1", False, when=OCT2)
    j.record("KO", "BUY", 12, 85.26, "level", "L1", False, when=OCT2 + 10)
    state = {"version": 1, "systems": [{
        "symbol": "KO", "num_levels": 5, "drawdown": 0.02, "status": "On", "position": 23, "entry_price": 87.0,
        "levels": [{"level": 1, "price": 85.26, "status": "filled", "order_id": "L1"},
                   {"level": 2, "price": 83.52, "status": "placed", "order_id": "L2"}],
        "trend_sma": 200, "trend_exit": True, "order_usd": 1000}]}
    f.write_text(json.dumps(state), encoding="utf-8")
    e = Engine(FakeBroker(), FakeMarketData(), str(f), events=queue.Queue(), journal=j)
    s = e.systems["KO"]
    assert (s.entry_qty, s.entry_ts, s.levels[0].qty, s.accounted_qty()) == (11, OCT2, 12, 23)


def test_cli_import_trades(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("QUANTDESK_MODE", "DRY_RUN")
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "DUO844164")
    monkeypatch.setenv("QUANTDESK_EXECUTIONS_FILE", str(tmp_path / "ex.jsonl"))
    raw = tmp_path / "trades.json"
    raw.write_text(json.dumps([{"execution_id": "e1", "order_id": 410323124, "symbol": "KO", "side": "B", "size": 12,
                                "price": "85.26", "trade_time_r": 1790953103000, "account": "DUO844164"}]), encoding="utf-8")
    assert cli.main(["import-trades", str(raw)]) == 0
    assert cli.main(["import-trades", str(raw)]) == 0
    out = capsys.readouterr().out
    assert "davon 1 neu" in out and "davon 0 neu" in out
    assert len(ExecutionArchive(str(tmp_path / "ex.jsonl")).load()) == 1
