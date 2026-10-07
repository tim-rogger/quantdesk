"""Server-Betrieb: TWS-Adapter (gemockt), nicht handelbare Symbole, Abgleichslauf, Kalender/Scheduler,
Dashboard inkl. STOP-ALL mit PIN, Push, Status/Snapshots, Order-Register, run_daily, close."""
import datetime as dt
import json
import queue
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

import forward_test
import run_daily
import scheduler
from dashboard.app import MAX_PIN_FAILS, create_app
from quantdesk.app import Services
from quantdesk.broker.base import BrokerError, NotTradableError, Position
from quantdesk.broker.tws import TwsBroker
from quantdesk.config import load_settings
from quantdesk.engine import Engine
from quantdesk.journal import Fill, Journal
from quantdesk.notify import Notifier
from quantdesk.registry import BotOrder, OrderRegistry, orders_from_log
from quantdesk.schedule import NEW_YORK, is_trading_day
from quantdesk.status import STOP_FILE, build_status, c_budget, read_jsonl, virtual_account, write_snapshot
from quantdesk.strategy import EquitySystem
from tests.fakes import FakeBroker, FakeMarketData

UTC = dt.timezone.utc


# ====================================================================== TWS-Adapter
class Event(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self

    def emit(self, *args):
        for fn in self:
            fn(*args)


class FakeIB:
    """Nachbau der benutzten ib_async-Schnittstelle."""

    def __init__(self, accounts=("DUO844164",), reject=None):
        self.errorEvent = Event()
        self.connected = False
        self.accounts = list(accounts)
        self.reject = reject  # (code, text) -> Order wird abgelehnt
        self.warn = None  # (code, text) -> Warnung, Order trotzdem aktiv
        self.placed, self.cancelled = [], []
        self.open, self.completed, self.fills = [], [], []
        self.pos = []
        self.values = []
        self._next = 1
        self.md_type = None

    def isConnected(self):
        return self.connected

    def connect(self, host, port, clientId, timeout):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def managedAccounts(self):
        return self.accounts

    def reqMarketDataType(self, t):
        self.md_type = t

    def sleep(self, s=0):
        pass

    def qualifyContracts(self, c):
        c.conId = 1000 + len(c.symbol)
        return [c]

    def positions(self, account=""):
        return self.pos

    def accountValues(self, account=""):
        return self.values

    def reqAllOpenOrders(self):
        return list(self.open)

    def reqCompletedOrders(self, apiOnly):
        return list(self.completed)

    def reqExecutions(self, flt=None):
        return list(self.fills)

    def reqTickers(self, *contracts):
        return [NS(last=float("nan"), close=99.5, marketPrice=lambda: float("nan"))]

    def placeOrder(self, contract, order):
        order.orderId = self._next
        self._next += 1
        status = NS(status="PendingSubmit", filled=0, avgFillPrice=0, permId=0)
        trade = NS(contract=contract, order=order, orderStatus=status, fills=[])
        self.placed.append(trade)
        if self.reject:
            self.errorEvent.emit(order.orderId, *self.reject, contract)
            status.status = "Inactive"
        else:
            if self.warn:
                self.errorEvent.emit(order.orderId, *self.warn, contract)
            order.permId = 900000 + order.orderId
            status.status = "Submitted"
        return trade

    def cancelOrder(self, order):
        self.cancelled.append(order.permId)


def trade(perm, symbol, action, qty, otype, status, lmt=None, filled=0, avg=0, account="DUO844164"):
    return NS(contract=NS(symbol=symbol, conId=1), fills=[],
              order=NS(permId=perm, orderId=0, action=action, totalQuantity=qty, orderType=otype, lmtPrice=lmt,
                       account=account, filledQuantity=filled),
              orderStatus=NS(status=status, filled=filled, avgFillPrice=avg, permId=perm))


def tws(ib=None, account="DUO844164"):
    return TwsBroker("ib-gateway", 4004, 17, account, ib=ib or FakeIB(), order_wait=0.05)


def test_tws_accepts_only_paper_accounts():
    assert tws().auth_status()["account"] == "DUO844164"
    with pytest.raises(BrokerError, match="Paper"):
        tws(FakeIB(accounts=["U1234567"]), account="").auth_status()
    with pytest.raises(BrokerError, match="Paper"):
        tws(FakeIB(accounts=["DU1111111"]), account="DUO844164").auth_status()  # anderes Konto im Login
    b = tws()
    b.auth_status()
    assert b.ib.md_type == 3  # verzögerte Kurse


def test_tws_reads_positions_orders_executions():
    ib = FakeIB()
    ib.pos = [NS(contract=NS(symbol="KO", secType="STK", conId=7), position=23.0, avgCost=86.1),
              NS(contract=NS(symbol="BRK B", secType="STK", conId=8), position=0, avgCost=0)]
    ib.open = [trade(5001, "KO", "BUY", 12, "LMT", "Submitted", lmt=83.52)]
    ib.completed = [trade(5000, "KO", "BUY", 12, "LMT", "Filled", lmt=85.26, filled=12, avg=85.26),
                    trade(4999, "X", "SELL", 1, "MKT", "Filled", account="DU0000001")]
    ib.fills = [NS(contract=NS(symbol="KO"), time=dt.datetime(2026, 10, 2, 14, 58, tzinfo=UTC),
                   execution=NS(execId="e1", permId=5000, side="BOT", shares=12.0, price=85.26))]
    ib.values = [NS(tag="TotalCashBalance", value="420000", currency="BASE"),
                 NS(tag="NetLiquidationByCurrency", value="455000.5", currency="BASE")]
    b = tws(ib)
    assert [(p.symbol, p.qty) for p in b.get_positions()] == [("KO", 23.0)]
    orders = {o.order_id: o for o in b.get_orders()}
    assert set(orders) == {"5000", "5001"}  # fremdes Konto ignoriert
    assert orders["5000"].is_filled and orders["5000"].avg_fill_price == 85.26 and orders["5001"].is_open
    [e] = b.get_executions()
    assert (e.order_id, e.side, e.qty, e.price) == ("5000", "BUY", 12.0, 85.26)
    assert dt.datetime.fromtimestamp(e.ts, UTC).date() == dt.date(2026, 10, 2)
    assert b.get_cash() == 420000 and b.get_net_liquidation() == 455000.5
    assert b.get_last_price("KO") == 99.5  # kein Last -> Close


def test_tws_order_placed_with_warning_is_reported_not_confirmed():
    ib = FakeIB()
    ib.warn = (399, "Order Message: Warning: your order will not be placed at the exchange until 09:30")
    r = tws(ib).place_limit_order("KO", "BUY", 12, 85.264)
    assert r.order_id == "900001" and r.status == "Submitted"
    assert r.messages and "399" in r.messages[0]
    assert ib.placed[0].order.lmtPrice == 85.26 and ib.placed[0].order.tif == "GTC"


def test_tws_rejected_order_counts_as_not_placed():
    ib = FakeIB(reject=(201, "Order rejected - reason: No trading permissions for this exchange"))
    with pytest.raises(NotTradableError):
        tws(ib).place_market_order("MA", "BUY", 3)
    ib = FakeIB(reject=(10148, "Order cannot be accepted"))
    with pytest.raises(BrokerError):
        tws(ib).place_market_order("KO", "BUY", 3)


def test_tws_cancel_by_perm_id():
    ib = FakeIB()
    ib.open = [trade(5001, "KO", "BUY", 12, "LMT", "Submitted", lmt=83.52)]
    b = tws(ib)
    b.cancel_order("5001")
    assert ib.cancelled == [5001]
    with pytest.raises(BrokerError):
        b.cancel_order("1")


# ====================================================================== Engine: nicht handelbar, Abgleichslauf
class PermissionBroker(FakeBroker):
    def place_market_order(self, symbol, side, qty):
        if symbol == "MA":
            raise NotTradableError("No trading permissions")
        return super().place_market_order(symbol, side, qty)


def engine(tmp_path, broker):
    return Engine(broker, FakeMarketData(), str(tmp_path / "eq.json"), events=queue.Queue(),
                  trend=lambda s, n: True, journal=Journal(str(tmp_path / "j.jsonl")),
                  registry=OrderRegistry(str(tmp_path / "reg.jsonl")))


def test_not_tradable_symbol_is_marked_and_skipped(tmp_path):
    b = PermissionBroker()
    e = engine(tmp_path, b)
    for sym in ("MA", "KO"):
        e.add_system(sym, 3, 0.02, trend_sma=200, trend_exit=True, order_usd=1000)
    e.toggle(["MA", "KO"])
    e.run_once()
    assert e.systems["MA"].not_tradable and not e.systems["KO"].not_tradable
    e.run_once()
    assert [p[1] for p in b.placed] == ["KO"]
    assert c_budget(e.systems.values()) == 4000  # MA zählt nicht zum Budget


def test_reconcile_only_run_books_fills_but_places_nothing(tmp_path):
    b = FakeBroker()
    e = engine(tmp_path, b)
    e.add_system("KO", 3, 0.02, trend_sma=200, trend_exit=True, order_usd=1000)
    e.toggle(["KO"])
    e.run_once(decide=False)
    assert b.placed == []
    e.run_once()
    b.fill("O1", 100.0)
    e.run_once(decide=False)  # Einstieg wird gebucht, aber noch keine Levels
    assert e.systems["KO"].entry_price == 100.0 and len(b.placed) == 1
    assert [f.kind for f in e.journal.read()] == ["entry"]
    assert e.registry.get("O1").kind == "entry"


# ====================================================================== Kalender & Scheduler
def test_trading_calendar():
    assert is_trading_day(dt.date(2026, 10, 7))
    assert not is_trading_day(dt.date(2026, 10, 10))  # Samstag
    assert not is_trading_day(dt.date(2026, 11, 26))  # Thanksgiving
    assert not is_trading_day(dt.date(2026, 12, 25))  # Weihnachten


def test_scheduler_next_run():
    ny = lambda *a: dt.datetime(*a, tzinfo=NEW_YORK)
    assert scheduler.next_run(ny(2026, 10, 7, 9, 0)) == (ny(2026, 10, 7, 10, 0), "trade")
    assert scheduler.next_run(ny(2026, 10, 7, 10, 0)) == (ny(2026, 10, 7, 11, 0), "watchdog")
    assert scheduler.next_run(ny(2026, 10, 7, 11, 0)) == (ny(2026, 10, 7, 15, 30), "reconcile")
    assert scheduler.next_run(ny(2026, 10, 9, 16, 0)) == (ny(2026, 10, 12, 10, 0), "trade")  # Fr -> Mo, Wochenende leer
    assert scheduler.next_run(ny(2026, 11, 25, 16, 0)) == (ny(2026, 11, 26, 10, 0), "holiday")  # Thanksgiving: nur Ping
    assert scheduler.next_run(ny(2026, 11, 26, 10, 0)) == (ny(2026, 11, 27, 10, 0), "trade")
    # Sommerzeit: 10:00 New York ist im Winter 15:00 UTC, im Sommer 14:00 UTC
    assert scheduler.next_run(ny(2026, 12, 1, 9, 0))[0].astimezone(UTC).hour == 15


# ====================================================================== Dashboard
def settings_for(tmp_path, monkeypatch, pin="246810"):
    monkeypatch.setenv("QUANTDESK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DASHBOARD_PIN", pin)
    monkeypatch.setenv("QUANTDESK_MODE", "DRY_RUN")
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "")
    return load_settings()


def test_dashboard_status_and_static(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch)
    client = TestClient(create_app(s, Notifier()))
    assert client.get("/api/status").json()["missing"] is True
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "status.json").write_text(json.dumps({"mode": "PAPER", "systems": []}), encoding="utf-8")
    write_snapshot(str(tmp_path / "data" / "snapshots.jsonl"), {"day": "2026-10-07", "c_value": 294000, "spy": 760})
    data = client.get("/api/status").json()
    assert data["mode"] == "PAPER" and data["stop_active"] is False and len(data["snapshots"]) == 1
    assert "QuantDesk" in client.get("/").text
    assert client.get("/manifest.webmanifest").json()["display"] == "standalone"
    assert "serviceWorker" not in client.get("/sw.js").text and client.get("/sw.js").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_dashboard_stop_all_needs_pin(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch)
    push = Notifier()
    client = TestClient(create_app(s, push))
    assert client.post("/api/stop", json={"pin": "000000"}).status_code == 403
    assert not (tmp_path / "data" / STOP_FILE).exists()
    r = client.post("/api/stop", json={"pin": "246810"})
    assert r.status_code == 200 and (tmp_path / "data" / STOP_FILE).exists()
    assert client.get("/api/status").json()["stop_active"] is True
    assert push.sent and "STOP-ALL" in push.sent[-1]["title"]


def test_dashboard_pin_lockout_and_disabled_without_pin(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch)
    client = TestClient(create_app(s, Notifier()))
    for _ in range(MAX_PIN_FAILS):
        client.post("/api/stop", json={"pin": "1"})
    assert client.post("/api/stop", json={"pin": "246810"}).status_code == 429  # auch richtige PIN gesperrt
    s2 = settings_for(tmp_path / "b", monkeypatch, pin="")
    assert TestClient(create_app(s2, Notifier())).post("/api/stop", json={"pin": ""}).status_code == 403


# ====================================================================== Push
def test_notifier_posts_json_with_token():
    calls = []

    class Session:
        def post(self, url, json, headers, timeout):
            calls.append((url, json, headers))
            return NS(raise_for_status=lambda: None)

    n = Notifier("http://ntfy:80", "quantdesk", "tk_abc", session=Session())
    assert n.send("QuantDesk KO", "Level 1 gefüllt – Ü", "order")
    url, payload, headers = calls[0]
    assert url == "http://ntfy:80" and payload["topic"] == "quantdesk" and "Ü" in payload["message"]
    assert headers == {"Authorization": "Bearer tk_abc"}
    assert Notifier().send("x", "y") is False  # ohne URL: nichts senden


def test_notifier_never_raises():
    import requests

    class Down:
        def post(self, *a, **k):
            raise requests.ConnectionError("down")

    assert Notifier("http://ntfy", session=Down()).send("x", "y", "error") is False


# ====================================================================== Status, Snapshots, Register
def fill(sym, side, qty, price, ts=0.0, kind="level"):
    return Fill(ts, "2026-10-01", sym, side, qty, price, kind, f"{sym}{ts}")


def test_virtual_account():
    fills = [fill("KO", "BUY", 10, 100, 1, "entry"), fill("KO", "BUY", 10, 90, 2), fill("PFE", "BUY", 5, 20, 3, "entry"),
             fill("PFE", "SELL", 5, 22, 4, "exit")]
    acc = virtual_account(fills, {"KO": 95.0}, budget=10000, fee=1)
    assert acc["cash"] == pytest.approx(10000 - 1000 - 900 - 100 + 110 - 4)
    assert acc["positions"]["KO"]["qty"] == 20 and acc["positions"]["KO"]["avg"] == 95.0
    assert acc["value"] == pytest.approx(acc["cash"] + 20 * 95) and "PFE" not in acc["positions"]


def test_snapshot_one_per_day(tmp_path):
    p = str(tmp_path / "s.jsonl")
    write_snapshot(p, {"day": "2026-10-07", "c_value": 1})
    write_snapshot(p, {"day": "2026-10-06", "c_value": 0})
    write_snapshot(p, {"day": "2026-10-07", "c_value": 2})
    assert [(r["day"], r["c_value"]) for r in read_jsonl(p)] == [("2026-10-06", 0), ("2026-10-07", 2)]


def test_build_status(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch)
    b = FakeBroker()
    b.positions["KO"] = Position("KO", 50, 80.0)  # 39 fremde
    e = engine(tmp_path, b)
    e.add_system("KO", 5, 0.02, trend_sma=200, trend_exit=True, order_usd=1000)
    e.toggle(["KO"])
    e.run_once()
    b.fill("O1", 100.0)
    e.run_once()
    st = build_status(e, s, e.journal.read(), 455000.0)
    [ko] = st["systems"]
    assert ko["bot_qty"] == 10 and ko["broker_qty"] == 60 and len(ko["open_levels"]) == 5
    assert st["c_account"]["budget"] == 6000 and st["net_liquidation"] == 455000.0


def test_registry_from_log_and_idempotent(tmp_path):
    log_lines = [
        "2026-09-29 20:18:56 INFO quantdesk.engine: WBD: Einstieg Market-Buy 32 platziert – ID 410323117.",
        "2026-09-29 20:20:02 INFO quantdesk.engine: WBD: Limit-Buy 33 @ 30.23 (Level 1) platziert – ID 410323220.",
        "2026-10-07 20:20:02 INFO quantdesk.engine: CVS: Trend unter SMA200 – Market-Sell 24 platziert – ID 1704305131.",
        "2026-09-29 INFO quantdesk.engine: AAPL: Einstieg Market-Buy 3 platziert – ID DRY-1.",  # DRY ignorieren
        "irgendwas anderes",
    ]
    orders = orders_from_log(log_lines)
    assert [(o.symbol, o.kind, o.qty, o.level, o.price) for o in orders] == [
        ("WBD", "entry", 32, None, None), ("WBD", "level", 33, 1, 30.23), ("CVS", "exit", 24, None, None)]
    reg = OrderRegistry(str(tmp_path / "r.jsonl"))
    assert reg.add(orders[0]) and not reg.add(orders[0])
    assert OrderRegistry(str(tmp_path / "r.jsonl")).get("410323117").qty == 32


# ====================================================================== run_daily & close
def env(tmp_path, monkeypatch):
    for k, v in {"QUANTDESK_MODE": "DRY_RUN", "IBKR_ACCOUNT_ID": "", "QUANTDESK_DATA_FILE": tmp_path / "eq.json",
                 "QUANTDESK_JOURNAL_FILE": tmp_path / "j.jsonl", "QUANTDESK_REGISTRY_FILE": tmp_path / "reg.jsonl",
                 "QUANTDESK_EXECUTIONS_FILE": tmp_path / "ex.jsonl", "QUANTDESK_DATA_DIR": tmp_path / "data",
                 "NTFY_URL": ""}.items():
        monkeypatch.setenv(k, str(v))


def fake_services(tmp_path, broker):
    def build(settings, events=None, infer_missing=False):
        e = engine(tmp_path, broker)
        if "KO" not in e.systems:
            e.add_system("KO", 3, 0.02, trend_sma=200, trend_exit=True, order_usd=1000)
            e.toggle(["KO"])
        return Services(settings, broker, NS(get_quote=lambda s: None), e, None, False)
    return build


def test_run_daily_skips_non_trading_days(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    monkeypatch.setattr(run_daily, "is_trading_day", lambda d: False)
    assert run_daily.run("trade", push=False) == 0
    assert not (tmp_path / "data" / "runs.jsonl").exists()


def test_run_daily_trade_and_reconcile(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    b = FakeBroker()
    monkeypatch.setattr(run_daily, "build_services", fake_services(tmp_path, b))
    monkeypatch.setattr(run_daily, "load_report", lambda *a, **k: (_ for _ in ()).throw(ValueError("noch nicht")))
    monkeypatch.setattr(run_daily, "PASS_WAIT_SECONDS", 0)
    assert run_daily.run("reconcile", force=True, push=False) == 0
    assert b.placed == []
    assert run_daily.run("trade", force=True, push=False) == 0
    assert b.placed[0][0] == "MKT"
    status = json.loads((tmp_path / "data" / "status.json").read_text(encoding="utf-8"))
    assert status["systems"][0]["symbol"] == "KO" and len(status["runs"]) == 2
    assert [r["mode"] for r in read_jsonl(str(tmp_path / "data" / "runs.jsonl"))] == ["reconcile", "trade"]
    assert len(read_jsonl(str(tmp_path / "data" / "snapshots.jsonl"))) == 1


def test_run_daily_respects_stop_file(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / STOP_FILE).write_text("{}")
    b = FakeBroker()
    monkeypatch.setattr(run_daily, "build_services", fake_services(tmp_path, b))
    monkeypatch.setattr(run_daily, "load_report", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    assert run_daily.run("trade", force=True, push=False) == 0
    assert b.placed == []
    assert json.loads((tmp_path / "eq.json").read_text())["systems"][0]["status"] == "Off"


def test_run_daily_broker_down_exit_1(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)

    class Down(FakeBroker):
        def auth_status(self):
            raise BrokerError("IB Gateway nicht erreichbar")

    monkeypatch.setattr(run_daily, "build_services", fake_services(tmp_path, Down()))
    monkeypatch.setattr(run_daily, "load_report", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    assert run_daily.run("trade", force=True, push=False) == 1
    [row] = read_jsonl(str(tmp_path / "data" / "runs.jsonl"))
    assert row["ok"] is False and any("nicht erreichbar" in e for e in row["errors"])


def test_close_system_after_cash_merger(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    from quantdesk import storage

    s = EquitySystem("WBD", 5, 0.02, status="On", trend_sma=200, trend_exit=True, order_usd=1000,
                     entry_price=30.85, entry_qty=32)
    storage.save(str(tmp_path / "eq.json"), {"WBD": s})
    assert forward_test.main(["close", "WBD", "--price", "31.01666668", "--date", "2026-10-06",
                              "--note", "Übernahme durch Paramount Skydance, 31.0167 $ bar je Aktie"]) == 0
    [f] = Journal(str(tmp_path / "j.jsonl")).read()
    assert (f.side, f.qty, f.price, f.kind, f.day) == ("SELL", 32, 31.01666668, "corporate_action", "2026-10-06")
    w = storage.load(str(tmp_path / "eq.json"))["WBD"]
    assert w.status == "Off" and w.closed.startswith("Übernahme") and w.bot_qty() == 0


def test_not_tradable_from_log():
    from quantdesk.registry import not_tradable_from_log

    lines = ["2026-09-29 ERROR quantdesk.engine: MA: IBKR hat die Order abgelehnt: No trading permissions.",
             "2026-09-29 ERROR quantdesk.engine: KO: IBKR hat die Order abgelehnt: Price too far.",
             "2026-09-29 ERROR quantdesk.engine: ZM: IBKR hat die Order abgelehnt: No trading permissions."]
    assert not_tradable_from_log(lines) == {"MA", "ZM"}
