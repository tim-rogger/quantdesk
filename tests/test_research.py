import math

import pytest

import research as cli
from quantdesk.backtest import Costs, GridParams
from quantdesk.history import Bar
from quantdesk.metrics import max_drawdown, perf
from quantdesk.research import (
    Period,
    _align,
    calendar,
    grid_portfolio,
    hold_single,
    load_universe,
    momentum_portfolio,
    periods,
    study,
    trading_range,
)

NO_FEE = Costs(1000, 0)


def days(n, start=0):
    # fortlaufende, sortierbare Tages-Strings
    return [f"D{i:05d}" for i in range(start, start + n)]


def series(closes, start=0):
    return [Bar(d, c, c, c, c) for d, c in zip(days(len(closes), start), closes)]


# ------------------------------------------------------------------ metrics
def test_perf_constant_growth():
    values = [100 * 1.001 ** i for i in range(253)]  # 252 Tage à +0.1 %
    p = perf(values)
    assert p.years == pytest.approx(1.0)
    assert p.cagr == pytest.approx(1.001 ** 252 - 1)
    assert p.volatility == pytest.approx(0.0, abs=1e-12)
    assert p.max_drawdown == 0.0 and p.avg_invested == 1.0


def test_perf_sharpe_drawdown_and_exposure():
    values = [100, 110, 99, 121, 110]
    assert max_drawdown(values) == pytest.approx(99 / 110 - 1)
    rets = [0.1, -0.1, 121 / 99 - 1, 110 / 121 - 1]
    mean = sum(rets) / 4
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / 4)
    p = perf(values, [50, 55, 49.5, 60.5, 55])
    assert p.sharpe == pytest.approx(mean * 252 / (sd * math.sqrt(252)))
    assert p.avg_invested == pytest.approx(0.5)
    assert p.return_on_invested == pytest.approx(p.cagr / 0.5)
    with pytest.raises(ValueError):
        perf([100])


# ------------------------------------------------------------------ Kalender & Zeiträume
def test_calendar_periods_and_trading_range():
    a, b = series([1.0] * 300), series([1.0] * 100, start=250)
    cal = calendar([a, b])
    assert len(cal) == 350 and cal == sorted(cal)
    train, test, total = periods(cal, warmup=50)
    assert (train.start, train.end, test.start, total.end) == (cal[50], cal[199], cal[200], cal[-1])
    assert trading_range(a, train, warmup=50) == (50, 200)
    # b beginnt spät und braucht erst eigenen Vorlauf -> im Training nicht handelbar
    assert trading_range(b, train, warmup=50) is None
    assert trading_range(b, test, warmup=50) == (50, 100)


def test_align_forward_fills():
    assert _align(["D2", "D4"], [10, 20], ["D1", "D2", "D3", "D4", "D5"], before=5) == [5, 10, 10, 20, 20]


def test_load_universe_skips_comments(tmp_path):
    f = tmp_path / "u.csv"
    f.write_text("# Kommentar\nsymbol,name,group\naapl,Apple,largecap\nBRK.B,Berkshire,largecap\n", encoding="utf-8")
    assert load_universe(str(f)) == ["AAPL", "BRK.B"]


# ------------------------------------------------------------------ Momentum
def test_momentum_picks_strongest_positive_without_lookahead():
    n = 80
    data = {
        "UP": series([100 * 1.01 ** i for i in range(n)]),
        "FLAT": series([100.0] * n),
        "DOWN": series([100 * 0.99 ** i for i in range(n)]),
    }
    cal = calendar(list(data.values()))
    period = Period("P", cal[70], cal[-1])
    r = momentum_portfolio(data, period, cal, NO_FEE, top_n=2, lookback=63, capital=1000)
    # Nur UP hat positives Momentum -> alles in UP, FLAT/DOWN nie gekauft
    assert r.trades == 1
    assert r.perf.total_return == pytest.approx(1.01 ** 9 - 1)  # Kauf zum Open von Tag 70, Close Tag 79
    assert r.perf.avg_invested == pytest.approx(1.0)


def test_momentum_goes_to_cash_when_nothing_rises():
    data = {"A": series([100 * 0.99 ** i for i in range(80)])}
    cal = calendar(list(data.values()))
    r = momentum_portfolio(data, Period("P", cal[70], cal[-1]), cal, NO_FEE, lookback=63, capital=1000)
    assert r.trades == 0 and r.perf.total_return == 0 and r.perf.avg_invested == 0


def test_momentum_rotates_only_at_month_start():
    # A steigt bis Tag 99 und fällt dann, B fällt bis Tag 99 und steigt dann stark.
    closes_a = [100 * 1.01 ** i for i in range(100)] + [100 * 1.01 ** 99 * 0.99 ** i for i in range(1, 101)]
    closes_b = [100 * 0.995 ** i for i in range(100)] + [100 * 0.995 ** 99 * 1.02 ** i for i in range(1, 101)]
    month = lambda i: 1 if i < 70 else 2 if i < 140 else 3  # Monatsanfänge an Tag 70 und 140
    names = [f"2000-{month(i):02d}-{i:03d}" for i in range(200)]  # [:7] = Monat wie bei echten Daten
    data = {
        "A": [Bar(d, c, c, c, c) for d, c in zip(names, closes_a)],
        "B": [Bar(d, c, c, c, c) for d, c in zip(names, closes_b)],
    }
    cal = calendar(list(data.values()))
    r = momentum_portfolio(data, Period("P", cal[70], cal[-1]), cal, NO_FEE, top_n=1, lookback=63, capital=1000)
    # Tag 70: A kaufen. Tag 100 (A dreht) ist kein Monatsanfang -> halten. Tag 140: A verkaufen, B kaufen.
    assert r.trades == 3
    value_140 = 1000 * closes_a[139] / closes_a[70] * closes_a[140] / closes_a[139]  # Verkauf zum Open Tag 140
    assert r.perf.total_return == pytest.approx(value_140 / 1000 * closes_b[199] / closes_b[140] - 1)


# ------------------------------------------------------------------ Portfolio & Studie
def test_grid_portfolio_sums_accounts_and_hold():
    data = {"A": series([100.0] * 300), "B": series([100.0 + i * 0.1 for i in range(300)])}
    cal = calendar(list(data.values()))
    period = Period("P", cal[252], cal[-1])
    grid, hold = grid_portfolio(data, GridParams(1, 0.5), NO_FEE, period, cal)
    assert grid.symbols == 2 and hold.symbols == 2
    assert grid.perf.avg_invested == pytest.approx(0.5, abs=0.01)  # je 1000 von 2000 investiert
    assert hold.perf.total_return > grid.perf.total_return > 0


def test_hold_single_and_study_rows():
    data = {s: series([100.0 + i * (k + 1) * 0.05 for i in range(400)]) for k, s in enumerate(["A", "B", "C"])}
    spy = series([100.0 + i * 0.05 for i in range(400)])
    cal = calendar(list(data.values()) + [spy])
    r = hold_single(spy, periods(cal)[2], cal, "SPY")
    assert r.perf.total_return == pytest.approx(spy[-1].close / spy[252].open - 1)
    out = study(data, spy, "SPY", NO_FEE, tuned_grid=GridParams(3, 0.05, take_profit=0.1))
    assert [k.split()[0] for k in out] == ["TRAINING", "TEST", "GESAMT"]
    names = [row.name for row in next(iter(out.values()))]
    assert names[0] == "Kaufen & Halten SPY (ETF)" and names[-1].startswith("Momentum")
    assert any("[im Training optimiert]" in n for n in names)


def test_research_cli_offline(monkeypatch, capsys, tmp_path):
    u = tmp_path / "u.csv"
    u.write_text("symbol,name,group\nA,A,x\nB,B,x\n", encoding="utf-8")
    data = {"A": series([100.0 + i * 0.1 for i in range(400)]), "B": series([100.0 + i * 0.2 for i in range(400)]),
            "SPY": series([100.0 + i * 0.1 for i in range(400)])}
    monkeypatch.setattr(cli, "load_history", lambda sym, years: data[sym])
    assert cli.main(["--universe", str(u), "--no-sweep"]) == 0
    out = capsys.readouterr().out
    assert "TEST" in out and "Momentum Top 5" in out and "Keine Anlageberatung" in out
