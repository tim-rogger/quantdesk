import json

import pytest

import backtest as cli
from quantdesk.backtest import Costs, GridParams, buy_and_hold, simulate, split, sweep
from quantdesk.history import Bar, load_history, parse_yahoo_history
from quantdesk.strategy import ValidationError

NO_FEE = Costs(order_usd=1000, fee=0)


def bars_from(prices):
    """(open, high, low, close)-Tupel -> Bars"""
    return [Bar(f"2020-01-{i + 1:02d}", *p) for i, p in enumerate(prices)]


def flat(n, price=100.0):
    return [(price, price, price, price)] * n


def test_uptrend_only_entry_is_bought_and_hold_wins():
    bars = bars_from([(100 + i, 101 + i, 100 + i, 101 + i) for i in range(20)])
    r = simulate(bars, GridParams(5, 0.02), NO_FEE)
    assert r.buys == 1 and r.max_capital == 1000 and r.open_position
    assert r.pnl == pytest.approx(1000 * 120 / 100 - 1000)
    assert r.hold_pnl == pytest.approx(6000 * 120 / 100 - 6000)
    assert r.excess < 0


def test_levels_filled_once_at_limit_price():
    bars = bars_from([(100, 100, 100, 100), (99, 99, 95, 96), (96, 96, 90, 92), (92, 93, 91, 92)])
    r = simulate(bars, GridParams(3, 0.02), NO_FEE)  # Levels 98, 96, 94
    assert r.buys == 4
    shares = 1000 / 100 + 1000 / 98 + 1000 / 96 + 1000 / 94
    assert r.pnl == pytest.approx(shares * 92 - 4000)


def test_take_profit_realizes_and_restarts():
    bars = bars_from([(100, 100, 100, 100), (99, 99, 97.5, 98), (99, 110, 99, 108), (108, 108, 108, 108)])
    r = simulate(bars, GridParams(2, 0.02, take_profit=0.05), NO_FEE)
    avg = 2000 / (1000 / 100 + 1000 / 98)
    assert r.sells == 1 and r.cycles == 2  # nach dem Verkauf neuer Zyklus am nächsten Tag
    assert r.realized == pytest.approx(2000 * 0.05)
    assert r.max_capital == 2000
    assert avg * 1.05 < 110


def test_no_take_profit_on_entry_day_or_buy_day():
    # Tag 0: Einstieg, Hoch weit über TP. Tag 1: Nachkauf und Hoch über TP am selben Tag -> kein Verkauf
    bars = bars_from([(100, 130, 100, 100), (100, 130, 97, 100), (100, 100, 100, 100)])
    r = simulate(bars, GridParams(1, 0.02, take_profit=0.05), NO_FEE)
    assert r.sells == 0


def test_stop_loss_after_all_levels_and_no_restart():
    bars = bars_from([(100, 100, 100, 100), (97, 97, 95, 95), (90, 90, 80, 82), (82, 90, 82, 90)])
    params = GridParams(2, 0.02, stop_loss=0.10, restart=False)  # Levels 98, 96 -> Stop bei 86.40
    r = simulate(bars, params, NO_FEE)
    shares = 1000 / 100 + 1000 / 97 + 1000 / 96  # Level 98 wird zum tieferen Open 97 gefüllt
    assert r.sells == 1 and r.cycles == 1 and not r.open_position
    assert r.realized == pytest.approx(shares * 86.4 - 3000)
    assert r.pnl == r.realized


def test_fees_are_counted():
    bars = bars_from([(100, 100, 100, 100), (99, 99, 97.5, 98), (99, 110, 99, 108)])
    r = simulate(bars, GridParams(2, 0.02, take_profit=0.05), Costs(1000, 2.0))
    assert r.fees == 2.0 * (r.buys + r.sells)
    no_fee = simulate(bars, GridParams(2, 0.02, take_profit=0.05), NO_FEE)
    assert r.pnl < no_fee.pnl


def test_max_drawdown_and_buy_and_hold():
    bars = bars_from([(100, 100, 100, 100), (100, 100, 100, 80), (100, 100, 100, 120)])
    r = simulate(bars, GridParams(1, 0.5), NO_FEE)
    assert r.max_drawdown == pytest.approx(-200)
    assert buy_and_hold(bars, 2000, 1.0) == pytest.approx((1999 / 100) * 120 - 1 - 2000)


def test_invalid_params():
    with pytest.raises(ValidationError):
        GridParams(5, 0.25)  # 5 × 25 % >= 100 %
    with pytest.raises(ValidationError):
        GridParams(3, 0.02, take_profit=0)


def test_split_and_sweep_select_on_training_only():
    # Training: steigt (wenig Nachkaufen ist besser), Test: fällt und erholt sich
    train = [(100 + i, 101 + i, 100 + i, 101 + i) for i in range(10)]
    test = [(110, 110, 110, 110), (100, 100, 80, 85), (85, 120, 85, 118)]
    data = {"X": bars_from(train + test)}
    a, b = split(data["X"], train_fraction=0.77)
    assert len(a) == 10 and len(b) == 3
    grid = [GridParams(1, 0.01), GridParams(1, 0.10, take_profit=0.2), GridParams(5, 0.03)]
    res = sweep(data, grid, NO_FEE, train_fraction=0.77)
    assert [p for _, p in res.ranking][0] == res.best
    assert res.ranking[0][0] >= res.ranking[-1][0]
    assert res.train[0].bars == 10 and res.test[0].bars == 3


def _yahoo(stamps, o, h, l, c, adj):
    return {"chart": {"result": [{"timestamp": stamps, "indicators": {
        "quote": [{"open": o, "high": h, "low": l, "close": c}], "adjclose": [{"adjclose": adj}]}}]}}


def test_parse_yahoo_history_adjusts_and_skips_gaps():
    data = _yahoo([0, 86400, 172800], [10, None, 20], [11, 1, 22], [9, 1, 19], [10, 1, 20], [5, 1, 20])
    bars = parse_yahoo_history(data)
    assert len(bars) == 2
    assert bars[0] == Bar("1970-01-01", 5.0, 5.5, 4.5, 5.0)  # Faktor adj/close = 0.5
    with pytest.raises(ValueError):
        parse_yahoo_history({"chart": {"result": None}})


def test_load_history_uses_cache(tmp_path):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return _yahoo([0], [10], [11], [9], [10], [10])

    class Session:
        calls = 0

        def get(self, url, headers=None, timeout=None):
            Session.calls += 1
            assert "period1=" in url and "AAPL" in url
            return Resp()

    kw = dict(session=Session(), cache_dir=str(tmp_path), now=1_790_000_000)
    first = load_history("aapl", 10, **kw)
    second = load_history("AAPL", 10, **kw)
    assert first == second and Session.calls == 1
    assert json.loads(next(tmp_path.iterdir()).read_text())[0][0] == "1970-01-01"


def test_cli_runs_offline(monkeypatch, capsys):
    prices = [(100, 101, 97, 99)] * 30 + [(99, 115, 99, 114)] * 30
    monkeypatch.setattr(cli, "load_history", lambda sym, years: bars_from(prices))
    assert cli.main(["AAPL", "MSFT", "--tp", "10", "--split"]) == 0
    out = capsys.readouterr().out
    assert "Strategie: 5×2%, TP 10%" in out and "Halten" in out and "2. Hälfte" in out
    assert "Keine Anlageberatung" in out
    assert cli.main(["AAPL", "--sweep"]) == 0
    assert "TEST (2. Hälfte" in capsys.readouterr().out
    assert cli.main(["AAPL", "--levels", "10", "--drawdown", "15"]) == 2
