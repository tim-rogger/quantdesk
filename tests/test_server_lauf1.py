"""Regressionstests zum ersten Server-Lauf (08.10.2026): ib_async-Platzhalter 1.8e308 als Menge -> bot_qty inf,
27 gesperrte Symbole, nan in der Zusammenfassung, WBD ohne Kontraktdefinition, ntfy 403 pro Ereignis."""
import json
import math
import queue
from types import SimpleNamespace as NS

import pytest
from ib_async import order as ibo

import run_daily
from quantdesk import storage
from quantdesk.app import Services
from quantdesk.broker.base import Order, Position, UnknownContractError
from quantdesk.config import load_settings
from quantdesk.engine import Engine
from quantdesk.journal import Journal, valid_fill
from quantdesk.migrate import migrate
from quantdesk.notify import Notifier
from quantdesk.registry import OrderRegistry
from quantdesk.status import money, pct
from quantdesk.strategy import FILLED, PLACED, EquitySystem, build_levels, sane_qty
from tests.fakes import FakeBroker, FakeMarketData
from tests.test_server import FakeIB, env, tws

UNSET = ibo.UNSET_DOUBLE  # 1.7976931348623157e+308
KO_LEVELS = ["410323124", "410323125", "410323126", "410323127", "410323128"]


def legacy_state(path, level1_status="placed", level1_qty=None):
    """Zustand im Altformat wie auf dem Laptop: kein entry_qty, Levels ohne qty, kein Order-Register."""
    levels = [{"level": i + 1, "price": round(87.0 * (1 - 0.02 * (i + 1)), 2), "status": "placed", "order_id": oid}
              for i, oid in enumerate(KO_LEVELS)]
    levels[0]["status"] = level1_status
    if level1_qty is not None:
        levels[0]["qty"] = level1_qty
    ko = {"symbol": "KO", "num_levels": 5, "drawdown": 0.02, "status": "On", "position": 23.0, "entry_price": 87.0,
          "entry_order_id": None, "entry_order_time": None, "levels": levels, "simulated": False, "trend_sma": 200,
          "trend_exit": True, "order_usd": 1000.0, "exit_order_id": None, "exit_order_time": None}
    path.write_text(json.dumps({"version": 1, "systems": [ko]}), encoding="utf-8")


def legacy_journal(path, extra=()):
    rows = [{"ts": 1790705956.98, "day": "2026-09-29", "symbol": "KO", "side": "BUY", "qty": 11.0, "price": 87.0,
             "kind": "entry", "order_id": "410323098", "simulated": False}, *extra]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def ib_trade(perm, symbol, order, status, filled=0.0, avg=0.0):
    order.permId, order.account = perm, "DUO844164"
    return ibo.Trade(contract=NS(symbol=symbol, conId=1), order=order,
                     orderStatus=ibo.OrderStatus(status=status, permId=perm, filled=filled, avgFillPrice=avg),
                     fills=[], log=[])


def no_floats_out_of_range(text):
    """Alle Zahlen in einer JSON- bzw. JSONL-Datei endlich und plausibel (kein nan/inf/1.8e308)."""
    def ok(v):
        if isinstance(v, dict):
            return all(ok(x) for x in v.values())
        if isinstance(v, list):
            return all(ok(x) for x in v)
        return not isinstance(v, float) or (math.isfinite(v) and abs(v) < 1e12)
    try:
        docs = [json.loads(text)]
    except json.JSONDecodeError:  # JSONL
        docs = [json.loads(line) for line in text.splitlines() if line.strip()]
    return all(ok(d) for d in docs)


# ====================================================================== Ursache: TWS-Adapter
def test_tws_open_order_with_ib_async_defaults_is_not_filled():
    """Order.filledQuantity ist bei offenen Orders UNSET_DOUBLE – das darf nie als Menge gelesen werden."""
    od = ibo.LimitOrder("BUY", 12, 83.52, tif="GTC")
    assert od.filledQuantity == UNSET  # Voraussetzung: so liefert ib_async offene Orders
    ib = FakeIB()
    ib.open.append(ib_trade(410323125, "KO", od, "Submitted"))
    [o] = tws(ib).get_orders()
    assert o.status == "Submitted" and o.filled_qty is None and o.limit_price == 83.52


def test_tws_filled_market_order_has_no_limit_and_real_qty():
    ib = FakeIB()
    ib.completed.append(ib_trade(410323098, "KO", ibo.MarketOrder("BUY", 11), "Filled", filled=11.0, avg=87.0))
    [o] = tws(ib).get_orders()
    assert (o.status, o.filled_qty, o.avg_fill_price, o.limit_price) == ("Filled", 11.0, 87.0, None)


def test_engine_never_books_placeholder_qty_even_if_broker_reports_filled(tmp_path):
    """Zweite Sicherung: meldet ein Broker 'Filled' mit 1.8e308, wird nichts gebucht und bot_qty bleibt endlich."""
    legacy_state(tmp_path / "eq.json")
    legacy_journal(tmp_path / "j.jsonl")
    b = FakeBroker()
    b.positions["KO"] = Position("KO", 11, 87.0)
    for oid, lv in zip(KO_LEVELS, storage.load(str(tmp_path / "eq.json"))["KO"].levels):
        b.orders[oid] = Order(oid, "KO", "BUY", 12, "LMT", lv.price, "Submitted")
    b.orders[KO_LEVELS[1]] = Order(KO_LEVELS[1], "KO", "BUY", 12, "LMT", 83.52, "Filled", filled_qty=UNSET)
    e = Engine(b, FakeMarketData(), str(tmp_path / "eq.json"), events=queue.Queue(), trend=lambda s, n: True,
               journal=Journal(str(tmp_path / "j.jsonl")), registry=OrderRegistry(str(tmp_path / "reg.jsonl")))
    e.run_once(decide=False)
    s = e.systems["KO"]
    assert math.isfinite(s.bot_qty()) and s.bot_qty() == 11
    assert s.accounted_qty() == 11
    assert no_floats_out_of_range((tmp_path / "eq.json").read_text(encoding="utf-8"))
    assert no_floats_out_of_range((tmp_path / "j.jsonl").read_text(encoding="utf-8"))
    msgs = [ev.message for ev in iter_events(e)]
    assert not any("eigene Fills ergeben" in m for m in msgs)
    assert any("unplausible Menge" in m for m in msgs)


def iter_events(engine):
    while not engine.events.empty():
        yield engine.events.get()


def test_reconcile_with_legacy_state_and_real_ib_async_orders(tmp_path):
    """Nachstellung Server-Lauf: Altformat-Zustand + IB Gateway mit echten ib_async-Objekten (offene GTC-Levels,
    Level 1 gefüllt). Ergebnis: bot_qty endlich und korrekt, keine Plausibilitätsfehler, nichts gesperrt."""
    legacy_state(tmp_path / "eq.json")
    legacy_journal(tmp_path / "j.jsonl")
    ib = FakeIB()
    ib.pos = [NS(contract=NS(symbol="KO", secType="STK", conId=1), position=23.0, avgCost=86.1)]
    ib.completed.append(ib_trade(int(KO_LEVELS[0]), "KO", ibo.LimitOrder("BUY", 12, 85.26), "Filled", 12.0, 85.26))
    for oid, price in zip(KO_LEVELS[1:], (83.52, 81.78, 80.04, 78.3)):
        ib.open.append(ib_trade(int(oid), "KO", ibo.LimitOrder("BUY", 12, price, tif="GTC"), "Submitted"))
    broker = tws(ib)
    e = Engine(broker, FakeMarketData(), str(tmp_path / "eq.json"), events=queue.Queue(), trend=lambda s, n: True,
               journal=Journal(str(tmp_path / "j.jsonl")), registry=OrderRegistry(str(tmp_path / "reg.jsonl")))
    e.run_once(decide=False)
    s = e.systems["KO"]
    assert s.entry_qty == 11 and s.levels[0].status == FILLED and s.levels[0].qty == 12
    assert [lv.status for lv in s.levels[1:]] == [PLACED] * 4
    assert s.bot_qty() == 23 and s.accounted_qty() == 23 and not s.blocked
    errors = [ev.message for ev in iter_events(e) if ev.level == "error"]
    assert errors == []
    assert no_floats_out_of_range((tmp_path / "eq.json").read_text(encoding="utf-8"))


# ====================================================================== harte Regel: nie nan/inf im Zustand
@pytest.mark.parametrize("bad", [UNSET, "Infinity", "NaN", -5])
def test_loading_state_with_invalid_qty_drops_value_and_blocks(tmp_path, bad):
    legacy_state(tmp_path / "eq.json", level1_status="filled", level1_qty=1.0)
    raw = (tmp_path / "eq.json").read_text(encoding="utf-8").replace('"qty": 1.0', f'"qty": {json.dumps(bad)}'
                                                                     if isinstance(bad, (int, float)) else f'"qty": {bad}')
    (tmp_path / "eq.json").write_text(raw, encoding="utf-8")
    s = storage.load(str(tmp_path / "eq.json"))["KO"]
    assert s.levels[0].qty is None and s.levels[0].status == PLACED  # Zustand unbekannt -> Abgleich klärt
    assert s.blocked.startswith("ungültige Werte im Zustand") and "Level 1 qty" in s.blocked
    assert not s.tradable and math.isfinite(s.bot_qty())
    storage.save(str(tmp_path / "eq2.json"), {"KO": s})
    assert no_floats_out_of_range((tmp_path / "eq2.json").read_text(encoding="utf-8"))


def test_bot_qty_and_accounted_qty_always_finite():
    s = EquitySystem("KO", 3, 0.02, entry_price=87.0, entry_qty=11, levels=build_levels(87.0, 3, 0.02))
    s.levels[0].status, s.levels[0].qty = FILLED, float("inf")
    s.levels[1].status, s.levels[1].qty = FILLED, 12.0
    s.sold_qty = float("nan")
    assert s.accounted_qty() == 23 and s.bot_qty() == 23
    s.entry_qty = UNSET
    assert s.accounted_qty() is None and s.bot_qty() == 0.0
    assert "Infinity" not in json.dumps(s.to_dict(), allow_nan=False)


def test_storage_refuses_nan():
    with pytest.raises(ValueError):
        json.dumps({"x": float("nan")}, allow_nan=False)
    assert sane_qty(12) and not sane_qty(UNSET) and not sane_qty(float("nan")) and not sane_qty(True)


def test_journal_refuses_and_skips_implausible_fills(tmp_path, caplog):
    j = Journal(str(tmp_path / "j.jsonl"))
    with pytest.raises(ValueError):
        j.record("KO", "BUY", UNSET, 85.26, "level", "X1", False)
    with pytest.raises(ValueError):
        j.record("KO", "BUY", 12, float("nan"), "level", "X2", False)
    j.record("KO", "BUY", 12, 85.26, "level", "X3", False)
    with open(tmp_path / "j.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": 1.0, "day": "2026-10-08", "symbol": "KO", "side": "BUY", "qty": UNSET,
                            "price": 85.26, "kind": "level", "order_id": "X4", "simulated": True}) + "\n")
    fills = j.read(include_simulated=True)
    assert [f.order_id for f in fills] == ["X3"] and len(j.invalid) == 1
    assert "unplausible" in caplog.text
    assert not valid_fill(UNSET, 1) and not valid_fill(1, 0) and valid_fill(1, 1)


def test_qty_without_price_skips_round(tmp_path):
    b = FakeBroker()
    e = Engine(b, FakeMarketData(), str(tmp_path / "eq.json"), events=queue.Queue(), trend=lambda s, n: True,
               journal=Journal(str(tmp_path / "j.jsonl")))
    e.add_system("KO", 3, 0.02, trend_sma=200, trend_exit=True, order_usd=1000)
    s = e.systems["KO"]
    assert e._qty(s, None) is None and e._qty(s, float("nan")) is None and e._qty(s, 0) is None
    assert e._qty(s, 100.0) == 10


# ====================================================================== Zusammenfassung
def test_money_and_pct_never_show_nan():
    assert money(294000) == "294'000 $" and money(1234567.8) == "1'234'568 $"
    assert money(float("nan")) == "–" and money(float("inf")) == "–" and money(None) == "–"
    assert pct(0.0123) == "+1.2%" and pct(float("nan")) == "–"


def test_summary_without_nan_and_without_stray_apostrophe():
    status = {"c_account": {"value": float("nan"), "budget": 294000.0, "invested": float("inf")}}
    _, text = run_daily._summary("reconcile", __import__("datetime").date(2026, 10, 8), [], status)
    assert "C: – (Budget 294'000 $), investiert –." in text
    assert "nan" not in text and "inf" not in text and "$)'" not in text


# ====================================================================== Marktdaten
def test_tws_delayed_and_unknown_contract_reported_once_per_run():
    ib = FakeIB()
    broker = tws(ib)
    for sym in ("KO", "PFE", "KO"):
        ib.errorEvent.emit(5, 10167, "Requested market data is not subscribed. Displaying delayed market data.",
                           NS(symbol=sym))
    ib.errorEvent.emit(7, 200, "No security definition has been found for the request", NS(symbol="WBD"))
    assert broker.notices() == ["Kurse von IBKR nur verzögert (kein Echtzeit-Abo) für 2 Symbol(e).",
                                "Keine Kontraktdefinition bei IBKR: WBD."]
    calls = []
    ib.qualifyContracts = lambda c: calls.append(c) or [c]
    with pytest.raises(UnknownContractError):
        broker.get_last_price("WBD")
    assert calls == []  # wird nicht erneut angefragt


def test_tws_unqualified_contract_is_cached_as_unknown():
    ib = FakeIB()
    calls = []

    def qualify(c):
        calls.append(c.symbol)
        return [None]

    ib.qualifyContracts = qualify
    broker = tws(ib)
    for _ in range(2):
        with pytest.raises(UnknownContractError):
            broker.get_last_price("WBD")
    assert calls == ["WBD"] and broker.unknown_symbols == {"WBD"}


def test_engine_blocks_symbol_without_contract_and_stops_asking(tmp_path):
    class MD(FakeMarketData):
        def __init__(self):
            super().__init__()
            self.broker = NS(unknown_symbols=set())
            self.asked = []

        def get_quote(self, symbol):
            self.asked.append(symbol)
            if symbol == "WBD":
                self.broker.unknown_symbols.add("WBD")
                return None
            return super().get_quote(symbol)

    md = MD()
    e = Engine(FakeBroker(), md, str(tmp_path / "eq.json"), events=queue.Queue(), trend=lambda s, n: True,
               journal=Journal(str(tmp_path / "j.jsonl")))
    for sym in ("WBD", "KO"):
        e.add_system(sym, 3, 0.02, trend_sma=200, trend_exit=True, order_usd=1000)
    e.toggle(["WBD", "KO"])
    e.run_once()
    assert e.systems["WBD"].blocked.startswith("keine Kontraktdefinition") and not e.systems["KO"].blocked
    md.asked.clear()
    e.run_once()
    assert "WBD" not in md.asked
    assert storage.load(str(tmp_path / "eq.json"))["WBD"].blocked  # bleibt gespeichert


# ====================================================================== Push
def test_notifier_403_warns_once_then_disabled(caplog):
    import requests

    calls = []

    class Forbidden:
        def post(self, *a, **k):
            calls.append(1)
            resp = NS(status_code=403)
            return NS(raise_for_status=lambda: (_ for _ in ()).throw(requests.HTTPError("403", response=resp)))

    n = Notifier("http://ntfy:80", "quantdesk", "", session=Forbidden())
    assert [n.send("x", str(i)) for i in range(5)] == [False] * 5
    assert len(calls) == 1 and n.failures == 5
    assert caplog.text.count("abgelehnt (403)") == 1 and "NTFY_TOKEN fehlt" in caplog.text


def test_notifier_other_errors_logged_only_first_time(caplog):
    import requests

    class Down:
        def post(self, *a, **k):
            raise requests.ConnectionError("down")

    n = Notifier("http://ntfy", session=Down())
    for _ in range(3):
        n.send("x", "y")
    assert caplog.text.count("fehlgeschlagen") == 1 and n.failures == 3


# ====================================================================== Migration
def migration_setup(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    legacy_state(tmp_path / "eq.json", level1_status="filled")
    bad = {"ts": 1790900000.0, "day": "2026-10-08", "symbol": "KO", "side": "BUY", "qty": UNSET, "price": 83.52,
           "kind": "level", "order_id": KO_LEVELS[1], "simulated": True}
    legacy_journal(tmp_path / "j.jsonl", [bad])
    (tmp_path / "quantdesk.log").write_text(
        "2026-09-29 ERROR quantdesk.engine: MA: IBKR hat die Order abgelehnt: No trading permissions.\n",
        encoding="utf-8")
    b = FakeBroker()
    b.positions["KO"] = Position("KO", 23, 86.1)
    b.orders[KO_LEVELS[0]] = Order(KO_LEVELS[0], "KO", "BUY", 12, "LMT", 85.26, "Filled", avg_fill_price=85.26,
                                   filled_qty=12, filled_at=1790800000.0)
    for oid, price in zip(KO_LEVELS[1:], (83.52, 81.78, 80.04, 78.3)):
        b.orders[oid] = Order(oid, "KO", "BUY", 12, "LMT", price, "Submitted")

    def build(settings, events=None, infer_missing=False):
        e = Engine(b, FakeMarketData(), settings.data_file, events=queue.Queue(), trend=lambda s, n: True,
                   journal=Journal(settings.journal_file), registry=OrderRegistry(settings.registry_file),
                   infer_missing=infer_missing)
        return Services(settings, b, NS(get_quote=lambda s: None), e, None, False)

    return load_settings(), b, build


def snapshot(tmp_path):
    return {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}


def test_migrate_dry_run_writes_nothing_and_reports(tmp_path, monkeypatch):
    settings, b, build = migration_setup(tmp_path, monkeypatch)
    before = snapshot(tmp_path)
    report = migrate(settings, str(tmp_path / "quantdesk.log"), dry_run=True, build=build)
    assert snapshot(tmp_path) == before and not (tmp_path / "reg.jsonl").exists()
    text = "\n".join(report.lines())
    assert text.startswith("TROCKENLAUF")
    assert "1 unplausible Einträge entfernt" in text and "KO 11 (Journal)" in text
    assert report.unresolved == [] and b.placed == []


def test_migrate_cleans_and_following_reconcile_has_no_errors(tmp_path, monkeypatch):
    settings, b, build = migration_setup(tmp_path, monkeypatch)
    report = migrate(settings, str(tmp_path / "quantdesk.log"), build=build)
    assert report.journal_removed == 1 and report.unresolved == [] and len(report.backups) == 2
    assert no_floats_out_of_range((tmp_path / "j.jsonl").read_text(encoding="utf-8"))
    s = storage.load(str(tmp_path / "eq.json"))["KO"]
    assert s.entry_qty == 11 and s.levels[0].qty == 12 and s.bot_qty() == 23 and not s.blocked
    reg = OrderRegistry(str(tmp_path / "reg.jsonl")).all()
    assert set(KO_LEVELS) <= set(reg) and "410323098" in reg
    # danach: normaler Abgleichslauf ohne Fehler, nichts doppelt gebucht
    e = build(settings).engine
    e.run_once(decide=False)
    assert [ev.message for ev in iter_events(e) if ev.level == "error"] == []
    assert [(f.kind, f.qty) for f in Journal(settings.journal_file).read()] == [("entry", 11), ("level", 12)]
    assert Journal(settings.journal_file).read()[1].estimated
    assert b.placed == []


def test_close_refuses_legacy_state_and_is_idempotent(tmp_path, monkeypatch, capsys):
    import forward_test

    env(tmp_path, monkeypatch)
    s = EquitySystem("WBD", 5, 0.02, status="On", trend_sma=200, trend_exit=True, order_usd=1000, entry_price=30.85,
                     blocked="keine Kontraktdefinition bei IBKR (Übernahme/Delisting?)")
    storage.save(str(tmp_path / "eq.json"), {"WBD": s})
    args = ["close", "WBD", "--price", "31.01666668", "--date", "2026-10-06", "--note", "Übernahme"]
    assert forward_test.main(args) == 1 and "zuerst 'forward_test.py migrate'" in capsys.readouterr().out
    s.entry_qty = 32
    storage.save(str(tmp_path / "eq.json"), {"WBD": s})
    assert forward_test.main(args) == 0 and forward_test.main(args) == 0
    assert [f.qty for f in Journal(str(tmp_path / "j.jsonl")).read()] == [32]
    w = storage.load(str(tmp_path / "eq.json"))["WBD"]
    assert w.closed == "Übernahme" and w.blocked is None
