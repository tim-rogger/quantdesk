import pytest

import research as cli
from quantdesk.backtest import Costs
from quantdesk.history import Bar
from quantdesk.metrics import add_cash_interest, perf
from quantdesk.research import Period, calendar, dual_momentum, mix_row
from quantdesk.walkforward import (
    MIN_TEST_DAYS,
    SHARPE_MARGIN,
    TEST_DAYS,
    TRAIN_DAYS,
    Curve,
    Market,
    WFResult,
    check,
    default_strategies,
    exposure_mix,
    measure,
    stitch,
    walk_forward,
    windows,
)

NO_FEE = Costs(1000, 0)
NO_MARKET = Market()


def cal_days(n):
    return [f"D{i:05d}" for i in range(n)]


def curve(values, invested=None, start=0):
    return Curve(cal_days(len(values))[start:] if start == 0 else [f"D{i:05d}" for i in range(start, start + len(values))],
                 list(values), list(invested or values))


def wf(curves, name="x"):
    return WFResult(name, curves, stitch(curves), [""] * len(curves))


def bars(closes, days=None):
    days = days or [f"{2000 + i // 252}-{1 + (i % 252) // 21:02d}-{i % 21:02d}" for i in range(len(closes))]
    return [Bar(d, c, c * 1.005, c * 0.995, c) for d, c in zip(days, closes)]


# ------------------------------------------------------------------ Fenster & Zusammenhängen
def test_windows_roll_by_one_year():
    cal = cal_days(252 + TRAIN_DAYS + 3 * TEST_DAYS + 100)
    ws = windows(cal)
    assert len(ws) == 4  # 3 volle Testjahre + ein Rest von 100 Tagen (≥ 63)
    assert ws[0].train.start == cal[252] and ws[0].test.start == cal[252 + TRAIN_DAYS]
    assert ws[1].test.start == cal[252 + TRAIN_DAYS + TEST_DAYS]
    assert ws[-1].test.end == cal[-1]
    for a, b in zip(ws, ws[1:]):
        assert a.test.end < b.test.start
    assert len(windows(cal_days(252 + TRAIN_DAYS + TEST_DAYS + MIN_TEST_DAYS - 1))) == 1


def test_stitch_chains_windows():
    c = stitch([curve([100, 110]), curve([50, 60], [25, 30], start=2)])
    assert c.values == pytest.approx([100, 110, 132])  # zweites Fenster startet bei 110: 110 * 60/50
    assert c.invested == pytest.approx([100, 110, 66])
    assert c.days == ["D00000", "D00001", "D00003"]


# ------------------------------------------------------------------ Zinsen, Währung, Sharpe
def test_cash_interest_only_on_uninvested_part():
    values, invested = [1000.0] * 3, [400.0] * 3
    out = add_cash_interest(values, invested, [0.0, 0.01, 0.01])
    assert out == pytest.approx([1000, 1006, 1006 + (600 + 6) * 0.01])
    assert add_cash_interest(values, values, [0.0, 0.01, 0.01]) == values  # voll investiert -> kein Zins


def test_sharpe_uses_excess_return():
    values = [100 * 1.0004 ** i * (1.01 if i % 2 else 0.99) for i in range(253)]
    zero = perf(values)
    with_rf = perf(values, rf=[0.0] + [0.0002] * 252)
    assert with_rf.sharpe < zero.sharpe
    assert with_rf.cagr == zero.cagr  # Zins ändert nur die Sharpe, nicht die Rendite der Kurve


def test_market_rates_and_fx():
    m = Market(rates=(["D00001", "D00003"], [0.05, 0.02]), fx=(["D00000"], [0.9]))
    assert m.daily_rates(cal_days(5)) == pytest.approx([0, 0.05 / 252, 0.05 / 252, 0.05 / 252, 0.02 / 252])
    assert m.fx_rates(cal_days(2)) == [0.9, 0.9]
    cash_only = Curve(cal_days(253), [100.0] * 253, [0.0] * 253)
    m2 = Market(rates=(["D00000"], [0.0504]), fx=(["D00000"], [0.8]))
    p = measure(cash_only, m2)
    assert p.cagr == pytest.approx(0.0504, abs=0.002)  # reines Cash bringt ≈ den T-Bill-Zins
    assert measure(cash_only, m2, "CHF").cagr == pytest.approx(p.cagr, abs=1e-9)  # konstanter Kurs


def test_exposure_mix():
    spy = curve([100, 110, 99])
    mix = exposure_mix(spy, 0.25)
    assert mix.values == pytest.approx([100, 102.5, 102.5 * (1 + 0.25 * (99 / 110 - 1))])
    assert mix.invested[1] == pytest.approx(102.5 * 0.25)


# ------------------------------------------------------------------ Bestehen-Regel
def test_check_rules_all_four():
    smooth = [100 * 1.002 ** i for i in range(60)]  # ruhiger UND mehr Rendite als SPY
    bumpy = [100 * 1.001 ** i * (1.02 if i % 2 else 0.98) for i in range(60)]
    crash = [100 * 1.001 ** i * (0.5 if i >= 30 else 1) for i in range(60)]
    spy = wf([curve(bumpy), curve(bumpy), curve(bumpy)])
    v = check(wf([curve(smooth)] * 3), spy, NO_MARKET)
    assert (v.k1, v.k2, v.k3, v.k4, v.passed, v.windows_better) == (True, True, True, True, True, 3)
    v = check(wf([curve(smooth), curve(bumpy), curve(crash)]), spy, NO_MARKET)
    assert not v.k2 and not v.k3 and not v.passed


def test_k1_needs_margin_and_k4_needs_more_than_cash_mix():
    base = [100 * 1.0005 ** i * (1.01 if i % 2 else 0.99) for i in range(120)]
    spy = wf([curve(base)])
    same = check(wf([curve(base)]), spy, NO_MARKET)
    assert same.perf.sharpe == pytest.approx(measure(spy.stitched, NO_MARKET).sharpe)
    assert not same.k1  # gleich gut reicht nicht, es braucht +SHARPE_MARGIN
    assert SHARPE_MARGIN == 0.2
    # 25 % SPY + 75 % Cash ohne eigenen Vorteil: K4 scheitert (Rendite ≈ Mischung, nicht darüber)
    lazy = exposure_mix(spy.stitched, 0.25)
    v = check(wf([lazy]), spy, NO_MARKET)
    assert v.perf.avg_invested == pytest.approx(0.25, abs=0.01)
    assert not v.k4


# ------------------------------------------------------------------ 60/40 & Dual Momentum
def test_mix_row_rebalances_monthly():
    days = ["2000-01-01", "2000-01-02", "2000-02-01", "2000-02-02"]
    etfs = {"S": bars([100, 200, 200, 200], days), "B": bars([100, 100, 100, 100], days)}
    cal = calendar(list(etfs.values()))
    r = mix_row(etfs, {"S": 0.5, "B": 0.5}, Period("P", days[0], days[-1]), cal, "50/50", NO_FEE, capital=1000)
    assert r.values == pytest.approx((1000, 1500, 1500, 1500))
    assert r.trades == 4  # 2 Orders pro Monatsanfang


def _dm_data(n=400):
    up = [100 * 1.002 ** i for i in range(n)]
    weak = [100 * 1.0002 ** i for i in range(n)]
    down = [100 * 0.999 ** i for i in range(n)]
    cash = [100 * 1.0001 ** i for i in range(n)]
    return up, weak, down, cash


def test_dual_momentum_picks_strongest_or_goes_safe():
    up, weak, down, cash = _dm_data()
    etfs = {"US": bars(up), "INTL": bars(weak), "AGG": bars(weak), "BIL": bars(cash)}
    cal = calendar(list(etfs.values()))
    period = Period("P", cal[300], cal[-1])
    r = dual_momentum(etfs, ("US", "INTL"), "AGG", "BIL", period, cal, "DM", NO_FEE, capital=1000)
    assert r.trades == 1 and r.perf.avg_invested == pytest.approx(1.0)
    assert r.perf.total_return == pytest.approx(up[-1] / up[300] - 1)  # hält durchgehend US
    etfs["US"], etfs["INTL"] = bars(down), bars(down)
    r = dual_momentum(etfs, ("US", "INTL"), "AGG", "BIL", period, cal, "DM", NO_FEE, capital=1000)
    assert r.perf.total_return == pytest.approx(weak[-1] / weak[300] - 1)  # beide unter T-Bills -> AGG


# ------------------------------------------------------------------ Ende-zu-Ende
def trending(n, drift):
    return bars([100 * (1 + drift) ** i * (1.01 if i % 3 == 0 else 1.0) for i in range(n)])


def _etfs(n):
    return {s: trending(n, d) for s, d in
            {"SPY": 0.0003, "EFA": 0.0002, "EEM": 0.0001, "TLT": 0.00005, "GLD": 0.0002, "AGG": 0.0001,
             "BIL": 0.00008, "VT": 0.00025}.items()}


def test_walk_forward_end_to_end():
    n = 252 + TRAIN_DAYS + 2 * TEST_DAYS
    data = {"A": trending(n, 0.0005), "B": trending(n, 0.0002)}
    etfs = _etfs(n)
    wins, benches, results = walk_forward(data, etfs["SPY"], "SPY", NO_FEE, default_strategies(tune=False, etfs=etfs), etfs)
    assert len(wins) == 2
    assert [b.name for b in benches] == ["Kaufen & Halten SPY (ETF)", "Kaufen & Halten VT (Welt-ETF)", "60/40 SPY/AGG, monatlich"]
    names = [r.name for r in results]
    assert names[0] == "Kaufen & Halten, alle gleich gewichtet" and names[-1].startswith("Dual Momentum breit")
    v = check(results[0], benches[0], NO_MARKET, "CHF")
    assert len(v.window_sharpes) == 2


def test_walk_forward_needs_enough_data():
    short = trending(500, 0.001)
    with pytest.raises(ValueError):
        walk_forward({"A": short}, short, strategies={})


def test_cli_walk_forward_offline(monkeypatch, capsys, tmp_path):
    u = tmp_path / "u.csv"
    u.write_text("symbol,name,group\nA,A,x\nB,B,x\n", encoding="utf-8")
    n = 252 + TRAIN_DAYS + TEST_DAYS
    data = {"A": trending(n, 0.0005), "B": trending(n, 0.0002), **_etfs(n),
            "^IRX": bars([4.0] * n), "CHF=X": bars([0.9] * n)}
    monkeypatch.setattr(cli, "load_history", lambda sym, years: data[sym])
    assert cli.main(["--universe", str(u), "--walk-forward", "--no-sweep"]) == 0
    out = capsys.readouterr().out
    assert "WALK-FORWARD" in out and "in USD" in out and "in CHF" in out and "Vergleich mit 60/40" in out
    assert "Dual Momentum klassisch" in out and "K4" in out
