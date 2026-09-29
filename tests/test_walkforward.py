import pytest

import research as cli
from quantdesk.backtest import Costs
from quantdesk.history import Bar
from quantdesk.metrics import perf
from quantdesk.research import Row
from quantdesk.walkforward import (
    MIN_TEST_DAYS,
    TEST_DAYS,
    TRAIN_DAYS,
    WFResult,
    check,
    default_strategies,
    stitch,
    walk_forward,
    windows,
)

NO_FEE = Costs(1000, 0)


def cal_days(n):
    return [f"D{i:05d}" for i in range(n)]


def row(values, invested=None):
    return Row("x", perf(values, invested or values), 0, 1, tuple(values), tuple(invested or values))


def wf(sharpes_and_curve, name="x"):
    """WFResult aus festen Fenster-Kurven."""
    rows = [row(v) for v in sharpes_and_curve]
    return WFResult(name, rows, perf(*stitch(rows)), [""] * len(rows))


def test_windows_roll_by_one_year():
    cal = cal_days(252 + TRAIN_DAYS + 3 * TEST_DAYS + 100)
    ws = windows(cal)
    assert len(ws) == 4  # 3 volle Testjahre + ein Rest von 100 Tagen (≥ 63)
    assert ws[0].train.start == cal[252] and ws[0].test.start == cal[252 + TRAIN_DAYS]
    assert ws[1].test.start == cal[252 + TRAIN_DAYS + TEST_DAYS]
    assert ws[-1].test.end == cal[-1]
    for a, b in zip(ws, ws[1:]):
        assert a.test.end < b.test.start  # Testfenster überlappen nicht
    short = cal_days(252 + TRAIN_DAYS + TEST_DAYS + MIN_TEST_DAYS - 1)
    assert len(windows(short)) == 1  # angebrochener Rest < 63 Tage zählt nicht


def test_stitch_chains_windows():
    values, invested = stitch([row([100, 110]), row([50, 60], [25, 30])])
    assert values == pytest.approx([100, 110, 132])  # zweites Fenster startet bei 110: 110 * 60/50
    assert invested == pytest.approx([100, 110, 66])


def test_check_rules():
    up_smooth = [100 * 1.001 ** i for i in range(60)]
    up_bumpy = [100 * 1.001 ** i * (1.02 if i % 2 else 0.98) for i in range(60)]
    crash = [100 * 1.001 ** i * (0.5 if i >= 30 else 1) for i in range(60)]  # bleibender Absturz
    bench = wf([up_bumpy, up_bumpy, up_bumpy])
    good = wf([up_smooth, up_smooth, up_smooth])
    v = check(good, bench)
    assert (v.k1, v.k2, v.k3, v.passed, v.windows_better) == (True, True, True, True, 3)
    mixed = wf([up_smooth, up_bumpy, crash])  # nur 1 von 3 Jahren besser, dazu ein Crash
    v = check(mixed, bench)
    assert not v.k3 and not v.k2 and not v.passed


def trending(n, drift):
    closes = [100 * (1 + drift) ** i * (1.01 if i % 3 == 0 else 1.0) for i in range(n)]
    return [Bar(f"{2000 + i // 252}-{1 + (i % 252) // 21:02d}-{i % 21:02d}", c, c * 1.005, c * 0.995, c)
            for i, c in enumerate(closes)]


def test_walk_forward_end_to_end():
    n = 252 + TRAIN_DAYS + 2 * TEST_DAYS
    data = {"A": trending(n, 0.0005), "B": trending(n, 0.0002)}
    spy = trending(n, 0.0003)
    wins, bench, results = walk_forward(data, spy, "SPY", NO_FEE, default_strategies(tune=False))
    assert len(wins) == 2
    assert bench.name == "Kaufen & Halten SPY (ETF)" and len(bench.windows) == 2
    names = [r.name for r, _ in results]
    assert names[0] == "Kaufen & Halten, alle gleich gewichtet" and names[-1].startswith("Momentum")
    assert all(v.windows_total == 2 for _, v in results)


def test_walk_forward_needs_enough_data():
    short = trending(500, 0.001)
    with pytest.raises(ValueError):
        walk_forward({"A": short}, short, strategies={})


def test_cli_walk_forward_offline(monkeypatch, capsys, tmp_path):
    u = tmp_path / "u.csv"
    u.write_text("symbol,name,group\nA,A,x\nB,B,x\n", encoding="utf-8")
    n = 252 + TRAIN_DAYS + TEST_DAYS
    data = {"A": trending(n, 0.0005), "B": trending(n, 0.0002), "SPY": trending(n, 0.0003)}
    monkeypatch.setattr(cli, "load_history", lambda sym, years: data[sym])
    assert cli.main(["--universe", str(u), "--walk-forward", "--no-sweep"]) == 0
    out = capsys.readouterr().out
    assert "WALK-FORWARD" in out and "Sharpe je Testjahr" in out and "Massstab" in out
    assert ("BESTANDEN" in out) or ("durchgefallen" in out)
