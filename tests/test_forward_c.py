"""Kandidat C im Live-Betrieb: Trendfilter, Verkauf bei Trendbruch, Ordergrösse in $, Journal, Report."""
import json
import queue
import random

import pytest

import forward_test as cli
from quantdesk.backtest import Costs, GridParams, simulate, trend_ok_series
from quantdesk.broker.base import Order, Position
from quantdesk.broker.dry_run import DryRunBroker
from quantdesk.engine import Engine
from quantdesk.forward import (
    TEST_MONTHS,
    MONTH_DAYS,
    build_report,
    c_systems,
    live_curve,
    monthly_returns,
    setup_systems,
    technical_problems,
)
from quantdesk.history import Bar
from quantdesk.journal import Fill, Journal
from quantdesk.strategy import CANCELLED, PLACED, EquitySystem, ValidationError
from quantdesk.trend import TrendFilter, trend_ok_now
from quantdesk.walkforward import Market
from tests.fakes import FakeBroker, FakeMarketData


def day(i):
    return f"{2024 + i // 252}-{1 + (i % 252) // 21:02d}-{1 + i % 21:02d}"


def make_bars(closes):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append(Bar(day(i), o, max(o, c) * 1.004, min(o, c) * 0.996, c))
        prev = c
    return out


# ------------------------------------------------------------------ Trendfilter = Backtest-Regel
def test_live_trend_rule_equals_backtest_rule():
    rng = random.Random(7)
    closes = [100.0]
    for _ in range(400):
        closes.append(closes[-1] * (1 + rng.gauss(0.0003, 0.015)))
    bars = make_bars(closes)
    for sma in (5, 50, 200):
        series = trend_ok_series(bars, sma)
        for i in range(len(bars)):
            live = trend_ok_now([b.close for b in bars[:i]], sma)
            assert (live is True) == series[i], (sma, i)


def test_trend_filter_ignores_todays_bar_and_caches():
    bars = [Bar(f"2026-09-{d:02d}", 100, 100, 100, c) for d, c in [(24, 10), (25, 10), (26, 10), (29, 5)]]
    calls = []

    def loader(sym, years):
        calls.append(sym)
        return bars

    tf = TrendFilter(loader)
    tf.today = lambda: "2026-09-29"  # heutiger Bar (Close 5) darf nicht zählen
    assert tf("X", 3) is None  # nur 3 Schlusskurse vor heute, SMA3 braucht 4
    assert tf("X", 2) is False  # Vortag 10 ist nicht ÜBER dem Schnitt (10, 10)
    bars.insert(0, Bar("2026-09-23", 100, 100, 100, 8))
    tf2 = TrendFilter(loader)
    tf2.today = lambda: "2026-09-29"
    assert tf2("X", 3) is True  # Vortag 10 > Schnitt(8, 10, 10); der heutige Einbruch auf 5 zählt nicht
    tf2("X", 3)
    assert calls.count("X") == 3  # tf: SMA3 + SMA2, tf2: SMA3 nur einmal (Cache pro Tag)


def test_trend_filter_returns_none_on_error():
    def boom(sym, years):
        raise ValueError("keine Daten")
    assert TrendFilter(boom)("X", 200) is None


def test_system_validation():
    with pytest.raises(ValidationError):
        EquitySystem("X", 5, 0.02, trend_exit=True)
    with pytest.raises(ValidationError):
        EquitySystem("X", 5, 0.02, trend_sma=1)
    s = EquitySystem("X", 5, 0.02, trend_sma=200, trend_exit=True, order_usd=1000)
    assert EquitySystem.from_dict(json.loads(json.dumps(s.to_dict()))) == s
    assert s.trend_label == "SMA200 + Exit"


# ------------------------------------------------------------------ Engine mit Trend
def c_engine(broker, tmp_path, trend, price=100.0, clock=None):
    md = FakeMarketData(price)
    e = Engine(broker, md, str(tmp_path / "eq.json"), events=queue.Queue(),
               trend=lambda sym, sma: trend[0], journal=Journal(str(tmp_path / "j.jsonl")),
               **({"clock": clock} if clock else {}))
    e.add_system("AAPL", 3, 0.02, trend_sma=200, trend_exit=True, order_usd=1000)
    e.toggle(["AAPL"])
    return e, md


def test_no_entry_when_trend_down_or_unknown(tmp_path):
    b = FakeBroker()
    trend = [None]
    e, _ = c_engine(b, tmp_path, trend)
    e.run_once()
    assert b.placed == []
    trend[0] = False
    e.run_once()
    assert b.placed == []


def test_entry_sized_in_dollars_then_levels(tmp_path):
    b = FakeBroker()
    trend = [True]
    e, _ = c_engine(b, tmp_path, trend, price=250.0)
    e.run_once()
    assert b.placed == [("MKT", "AAPL", "BUY", 4, None)]  # 1000 $ / 250 $
    b.fill("O1", 250.0)
    e.run_once()
    assert [(o[3], o[4]) for o in b.placed[1:]] == [(4, 245.0), (4, 240.0), (4, 235.0)]


def test_trend_break_cancels_and_sells_then_reenters(tmp_path):
    b = FakeBroker()
    trend = [True]
    e, md = c_engine(b, tmp_path, trend)
    e.run_once()
    b.fill("O1", 100.0)
    e.run_once()  # 3 Levels platziert (O2..O4)
    s = e.systems["AAPL"]
    b.fill(s.levels[0].order_id, 98.0)
    e.run_once()  # Level 1 gefüllt
    trend[0] = False
    e.run_once()  # Trendbruch
    assert sorted(b.cancelled) == ["O3", "O4"]
    sell = b.orders_of_type("MKT")[-1]
    assert (sell.side, sell.qty) == ("SELL", 20)
    assert s.exit_order_id == sell.order_id and all(lv.status != PLACED for lv in s.levels)
    # Verkauf ausgeführt
    b.orders[sell.order_id] = Order(sell.order_id, "AAPL", "SELL", 20, "MKT", None, "Filled", avg_fill_price=95.0)
    del b.positions["AAPL"]
    e.run_once()
    assert s.entry_price is None and s.levels == [] and s.exit_order_id is None
    e.run_once()
    assert len(b.orders_of_type("MKT")) == 2  # kein neuer Einstieg im Abwärtstrend
    trend[0] = True
    e.run_once()
    assert len(b.orders_of_type("MKT")) == 3  # neuer Zyklus
    kinds = [(f.side, f.kind, f.qty, f.price) for f in e.journal.read()]
    assert kinds == [("BUY", "entry", 10, 100.0), ("BUY", "level", 10, 98.0), ("SELL", "exit", 20, 95.0)]


def test_exit_without_position_just_resets(tmp_path):
    b = FakeBroker()
    trend = [True]
    e, _ = c_engine(b, tmp_path, trend)
    e.run_once()  # Einstiegs-Order offen, noch nicht gefüllt
    trend[0] = False
    e.run_once()
    assert b.cancelled == ["O1"]
    assert e.systems["AAPL"].entry_order_id is None and not b.orders_of_type("MKT")[0].is_open


def test_dry_run_sells_only_own_shares_once(tmp_path):
    real = FakeBroker()
    real.positions["AAPL"] = Position("AAPL", 3, 90.0)  # fremde, echte Paper-Position
    dry = DryRunBroker(real, price_fn=lambda s: 100.0)
    trend = [True]
    e, _ = c_engine(dry, tmp_path, trend)
    e.run_once()  # eigener simulierter Einstieg: 10 Stück
    e.run_once()
    assert e.systems["AAPL"].bot_qty() == 10
    trend[0] = False
    for _ in range(4):
        e.run_once()
    assert real.placed == [] and real.cancelled == []
    sells = [o for o in dry.get_orders() if o.side == "SELL"]
    assert [(o.qty) for o in sells] == [10]  # nur die eigenen, nur einmal
    assert [(p.symbol, p.qty) for p in dry.get_positions()] == [("AAPL", 3)]  # fremde bleiben


# ------------------------------------------------------------------ Engine == Backtest (Tag für Tag)
class MarketBroker(FakeBroker):
    """Füllt Market-Orders zum Eröffnungskurs und Limit-Buys, wenn das Tagestief sie erreicht."""

    def fill_market(self, price):
        for o in list(self.orders.values()):
            if o.is_open and o.order_type == "MKT":
                self._fill(o, price)

    def fill_limits(self, bar):
        for o in list(self.orders.values()):
            if o.is_open and o.order_type == "LMT" and bar.low <= o.limit_price:
                self._fill(o, min(bar.open, o.limit_price))

    def _fill(self, o, price):
        self.orders[o.order_id] = Order(o.order_id, o.symbol, o.side, o.qty, o.order_type, o.limit_price, "Filled",
                                        avg_fill_price=price)
        old = self.positions.get(o.symbol)
        q = (old.qty if old else 0) + (o.qty if o.side == "BUY" else -o.qty)
        if q <= 0:
            self.positions.pop(o.symbol, None)
        else:
            cost = (old.qty * old.avg_price if old else 0) + (o.qty * price if o.side == "BUY" else -o.qty * old.avg_price)
            self.positions[o.symbol] = Position(o.symbol, q, cost / q)


def test_engine_trades_like_backtest(tmp_path):
    sma = 10
    rng = random.Random(3)
    closes, c = [], 100.0
    for i in range(260):
        drift = 0.004 if (i // 40) % 2 == 0 else -0.004  # Trendphasen
        c *= 1 + drift + rng.gauss(0, 0.012)
        closes.append(c)
    bars = make_bars(closes)
    params = GridParams(3, 0.02, trend_sma=sma, trend_exit=True)
    start = sma + 1
    bt = simulate(bars, params, Costs(1000, 0), "X", start)

    broker = MarketBroker()
    md = FakeMarketData()
    state = {"trend": None}
    e = Engine(broker, md, str(tmp_path / "eq.json"), events=queue.Queue(),
               trend=lambda s, n: state["trend"], journal=Journal(str(tmp_path / "j.jsonl")))
    e.add_system("X", 3, 0.02, trend_sma=sma, trend_exit=True, order_usd=1000)
    e.toggle(["X"])
    for i in range(start, len(bars)):
        state["trend"] = trend_ok_now([b.close for b in bars[:i]], sma)
        md.price = bars[i].open
        e.run_once()  # vor Börsenöffnung: Einstieg / Verkauf
        broker.fill_market(bars[i].open)
        e.run_once()  # nach Öffnung: Fill erkannt, Levels platziert
        broker.fill_limits(bars[i])
        e.run_once()  # Level-Fills erkannt
    fills = e.journal.read()
    assert bt.sells > 2 and bt.buys > bt.sells  # Testdaten enthalten mehrere Zyklen
    assert sum(f.side == "BUY" for f in fills) == bt.buys
    assert sum(f.side == "SELL" for f in fills) == bt.sells
    assert (bt.open_position) == ("X" in broker.positions)


# ------------------------------------------------------------------ Einrichtung & Report
def test_setup_systems_is_idempotent(tmp_path):
    f = str(tmp_path / "eq.json")
    added, skipped = setup_systems(f, ["AAPL", "MSFT"])
    assert (added, skipped) == (["AAPL", "MSFT"], [])
    assert setup_systems(f, ["AAPL", "KO"]) == (["KO"], ["AAPL"])
    systems = c_systems(f)
    assert len(systems) == 3 and all(s.status == "Off" and s.order_usd == 1000 for s in systems)


def fill(d, sym, side, qty, price, kind, oid, ts=0):
    return Fill(ts, d, sym, side, qty, price, kind, oid)


def test_live_curve_and_monthly_returns():
    bars = {"A": [Bar("D1", 10, 10, 10, 10), Bar("D2", 10, 12, 10, 12), Bar("D3", 12, 12, 11, 11)]}
    fills = [fill("D1", "A", "BUY", 10, 10, "entry", "1"), fill("D3", "A", "SELL", 10, 11.5, "exit", "2")]
    c = live_curve(fills, bars, ["D1", "D2", "D3"], budget=1000, fee=1)
    assert c.values == pytest.approx([999, 1019, 1013])  # 1000 - 100 - 1 + 10*10 / +10*12 / Verkauf 115 - 1
    assert c.invested == pytest.approx([100, 120, 0])
    assert monthly_returns(c, month_days=1) == pytest.approx([1019 / 999 - 1, 1013 / 1019 - 1])


def test_technical_problems():
    ok = [fill("D1", "A", "BUY", 1, 1, "entry", "1"), fill("D2", "A", "SELL", 1, 1, "exit", "2"),
          fill("D3", "A", "BUY", 1, 1, "entry", "3")]
    assert technical_problems(ok) == []
    bad = ok + [fill("D3", "A", "BUY", 1, 1, "entry", "3"), fill("D4", "B", "BUY", 1, 1, "entry", "4"),
                fill("D5", "B", "BUY", 1, 1, "entry", "5")]
    probs = technical_problems(bad)
    assert any("doppelt" in p for p in probs) and any("zweiter Einstieg" in p for p in probs)


def _report_data(n_days):
    closes = [100 * 1.001 ** i for i in range(260 + n_days)]
    bars = {"A": make_bars(closes)}
    spy = make_bars([100 * 1.0008 ** i for i in range(260 + n_days)])
    start = bars["A"][260].day
    return bars, spy, start


def test_build_report_running_and_finished():
    bars, spy, start = _report_data(30)
    fills = [fill(start, "A", "BUY", 10, bars["A"][260].open, "entry", "1")]
    r = build_report(fills, bars, spy, Market(), budget=6000)
    assert not r.finished and r.months_done == 1 and len(r.checks) == 5
    assert set(r.rows) >= {"C live (Paper)", "SPY halten", "C Backtest (gleicher Zeitraum)"}
    bars, spy, start = _report_data(TEST_MONTHS * MONTH_DAYS + 5)
    r = build_report([fill(start, "A", "BUY", 10, bars["A"][260].open, "entry", "1")], bars, spy, Market(), budget=6000)
    assert r.finished and r.checks[-1].ok
    with pytest.raises(ValueError):
        build_report([], bars, spy, Market(), budget=6000)


def test_cli_setup_and_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("QUANTDESK_MODE", "DRY_RUN")
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "")
    monkeypatch.setenv("QUANTDESK_DATA_FILE", str(tmp_path / "eq.json"))
    monkeypatch.setenv("QUANTDESK_JOURNAL_FILE", str(tmp_path / "j.jsonl"))
    u = tmp_path / "u.csv"
    u.write_text("symbol,name,group\nA,A,x\n", encoding="utf-8")
    assert cli.main(["setup", "--universe", str(u)]) == 0
    assert "neu angelegt (Status Off): 1" in capsys.readouterr().out
    bars, spy, start = _report_data(40)
    data = {"A": bars["A"], "SPY": spy, "^IRX": make_bars([4.0] * 300), "CHF=X": make_bars([0.9] * 300)}
    monkeypatch.setattr(cli, "load_history", lambda sym, years: data[sym])
    assert cli.main(["report"]) == 1  # noch keine Fills
    Journal(str(tmp_path / "j.jsonl")).record("A", "BUY", 10, 100, "entry", "1", simulated=True)
    assert cli.main(["report"]) == 1  # DRY-Fills zählen nur mit --include-dry
    assert cli.main(["report", "--include-dry", "--start", start]) == 0
    out = capsys.readouterr().out
    assert "VORWÄRTSTEST KANDIDAT C" in out and "F4" in out and "LÄUFT" in out
