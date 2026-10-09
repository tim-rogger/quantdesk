"""Forschungsphase 2: Strategie-Schnittstelle, Simulator, Kandidat D, Bestehen-Regel – NUR künstliche Daten.
(Ein Lauf auf echten Daten ist bis zur bestätigten Anmeldung gesperrt.)"""
import datetime as dt
import random

import pytest

from quantdesk import etf_strategies as es
from quantdesk import phase2 as p2
from quantdesk.allocation import Aligned, Costs, SimResult, View, month_ends, simulate
from quantdesk.history import Bar

UNI = ("AAA", "BBB", "CCC", "DDD")


def business_days(start="2000-01-03", n=1500):
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def series(days, drift, vol=0.01, seed=1, start_price=100.0):
    rnd, p, out = random.Random(seed), start_price, []
    for d in days:
        out.append(Bar(d, p, p, p, p))
        p *= 1 + drift + vol * rnd.gauss(0, 1)
    return out


def market_data(n=1500, end=None):
    days = business_days(n=n)
    bars = {"AAA": series(days, 0.0006, seed=1), "BBB": series(days, -0.0004, seed=2),
            "CCC": series(days, 0.0003, seed=3), "DDD": series(days, 0.0001, seed=4),
            "SPY": series(days, 0.0004, seed=5), "IEF": series(days, 0.0001, 0.003, seed=6)}
    rates = [(days[0], 0.02)]
    if end:
        bars = {k: [b for b in v if b.day <= end] for k, v in bars.items()}
    return Aligned(bars, rates), days


# ---------------------------------------------------------------- Schnittstelle & Simulator
@pytest.mark.parametrize("strategy", [es.TrendLong(UNI), es.TrendBarbell(UNI), es.TrendTopN(UNI, top_n=2),
                                      es.RiskParity(UNI), es.EqualWeight(UNI)])
def test_no_lookahead_weights_unchanged_when_future_removed(strategy):
    full, days = market_data()
    cut = days[1000]
    short, _ = market_data(end=cut)
    for i in sorted(month_ends(days)):
        if 300 < i <= 990:
            assert strategy.target_weights(View(full, i)) == strategy.target_weights(View(short, i)), days[i]


def test_simulate_trades_next_day_at_close_and_accrues_cash():
    data, days = market_data(n=400)
    res = simulate(es.Hold("SPY", "SPY"), data, days[10], costs=Costs(0.0, 0.0))
    assert res.weights_log[0][0] == days[10]
    assert res.invested[0] == 0 and res.values[1] == pytest.approx(100_000 * (1 + 0.02 / 252))  # Handel an Tag 2
    spy = {b.day: b.close for b in [x for x in series(business_days(n=400), 0.0004, seed=5)]}
    ratio = res.values[-1] / res.values[1]
    assert ratio == pytest.approx(spy[res.days[-1]] / spy[res.days[1]], rel=1e-9)  # danach exakt wie SPY
    cash = simulate(type("Cash", (), {"name": "Cash", "target_weights": lambda self, v: {}})(), data, days[10],
                    costs=Costs(0.0, 0.0))
    assert cash.values[-1] == pytest.approx(100_000 * (1 + 0.02 / 252) ** (len(cash.values) - 1))
    assert cash.orders == 0


def test_simulate_costs_and_monthly_rebalance():
    data, days = market_data(n=400)
    res = simulate(es.FixedMix((("SPY", 0.6), ("IEF", 0.4)), "60/40"), data, days[10], costs=Costs(1.0, 5.0))
    months = len([i for i in month_ends(days) if 10 <= i < len(days) - 1]) + 1
    assert len(res.weights_log) == months
    assert res.orders == 2 * months  # beide ETFs jeden Monat zurück auf 60/40
    assert res.costs > 2 * months * 1.0  # Kommission + Schlupf


def test_simulate_rejects_leverage_and_shorts():
    data, days = market_data(n=300)
    bad = type("Bad", (), {"name": "Hebel", "target_weights": lambda self, v: {"SPY": 1.5}})()
    with pytest.raises(ValueError, match="kein Hebel"):
        simulate(bad, data, days[10])
    short = type("Short", (), {"name": "Short", "target_weights": lambda self, v: {"SPY": -0.5}})()
    with pytest.raises(ValueError, match="long-only"):
        simulate(short, data, days[10])


# ---------------------------------------------------------------- Regeln von D
def view_with(closes_by_sym, rate=0.0):
    days = business_days(n=len(next(iter(closes_by_sym.values()))))
    bars = {s: [Bar(d, c, c, c, c) for d, c in zip(days, cl)] for s, cl in closes_by_sym.items()}
    data = Aligned(bars, [(days[0], rate)])
    return View(data, len(days) - 1)


def test_d1_invests_only_with_positive_long_trend():
    up = [100 * 1.001 ** i for i in range(300)]
    down = [100 * 0.999 ** i for i in range(300)]
    v = view_with({"AAA": up, "BBB": down, "CCC": up, "DDD": down})
    assert es.TrendLong(UNI).target_weights(v) == {"AAA": 0.25, "CCC": 0.25}
    # Trend muss den T-Bill schlagen: 2.5 % p.a. Aufwärtstrend reicht bei 5 % Zins nicht
    slow = [100 * 1.0001 ** i for i in range(300)]
    assert es.TrendLong(("AAA",)).target_weights(view_with({"AAA": slow}, rate=0.05)) == {}
    assert es.TrendLong(("AAA",)).target_weights(view_with({"AAA": slow[:200]})) == {}  # zu wenig Historie


def test_d2_barbell_half_positions():
    long_up_short_down = [100 * 1.002 ** i for i in range(260)] + [100 * 1.002 ** 260 * 0.995 ** i for i in range(40)]
    up = [100 * 1.001 ** i for i in range(300)]
    v = view_with({"AAA": long_up_short_down, "BBB": up})
    w = es.TrendBarbell(("AAA", "BBB")).target_weights(v)
    assert w == {"AAA": pytest.approx(0.25), "BBB": pytest.approx(0.5)}


def test_d3_top_n_picks_best_positive():
    mk = lambda g: [100 * g ** i for i in range(300)]  # noqa: E731
    v = view_with({"AAA": mk(1.003), "BBB": mk(1.002), "CCC": mk(1.001), "DDD": mk(0.999)})
    assert es.TrendTopN(UNI, top_n=3).target_weights(v) == {s: pytest.approx(1 / 3) for s in ("AAA", "BBB", "CCC")}
    v2 = view_with({"AAA": mk(1.003), "BBB": mk(0.998), "CCC": mk(0.999), "DDD": mk(0.999)})
    assert es.TrendTopN(UNI, top_n=3).target_weights(v2) == {"AAA": pytest.approx(1 / 3)}  # Rest Cash


def test_risk_parity_inverse_vol():
    rnd = random.Random(7)
    calm, wild = [100.0], [100.0]
    for _ in range(299):
        calm.append(calm[-1] * (1 + 0.002 * rnd.choice((-1, 1))))
        wild.append(wild[-1] * (1 + 0.008 * rnd.choice((-1, 1))))
    w = es.RiskParity(("AAA", "BBB")).target_weights(view_with({"AAA": calm, "BBB": wild}))
    assert w["AAA"] == pytest.approx(0.8, abs=1e-6) and sum(w.values()) == pytest.approx(1.0)


def test_parameters_fixed_as_registered():
    assert (es.LONG, es.SHORT, es.TOP_N, es.VOL_WINDOW) == (252, 42, 3, 126)
    assert [s.name for s in es.candidates_d()] == ["D1 Trend lang (12 M)", "D2 Trend kurz+lang (2 M + 12 M)",
                                                   "D3 Trend kurz+lang, beste 3"]


# ---------------------------------------------------------------- Bestehen-Regel & Sicht 2
def curve(name, days, daily):
    v = [100.0]
    for r in daily[1:]:
        v.append(v[-1] * (1 + r))
    return SimResult(name, days, v, v[:])


def test_verdict_and_normalized():
    days = business_days(n=252 * 4 + 1)
    rnd = random.Random(3)
    spy_r = [0.0] + [0.0004 + 0.012 * rnd.gauss(0, 1) for _ in days[1:]]
    spy = curve("SPY", days, spy_r)
    market = p2.Market([(days[0], 0.0)], [(days[0], 1.0)], [(days[0], 0.0)])
    same = p2.verdict(spy, spy, market, n_trials=1)
    assert not same.k1 and same.k2 and same.windows_total == 4 and same.windows_better == 0
    # halb SPY, halb Cash (Zins 0): Hebel ≈ 2, normiert ≈ SPY minus Finanzierungsaufschlag
    half = [0.0] + [r / 2 for r in spy_r[1:]]
    h = curve("halb", days, half)
    h.invested = [v / 2 for v in h.values]
    n = p2.normalized(h, spy, market, spread=0.015)
    assert n.leverage == pytest.approx(2.0, rel=1e-6)
    assert n.financing_cost == pytest.approx(0.015, rel=1e-6)
    assert n.cagr < n.spy_cagr and not n.r1
    v = p2.verdict(h, spy, market, n_trials=1)
    assert v.mix.cagr == pytest.approx(v.perf.cagr, abs=2e-3)  # 50 % SPY + Cash = genau diese Strategie
    assert p2.correlation(h, spy) == pytest.approx(1.0, abs=1e-3)  # Monatsrenditen: Zinseszins, fast linear


def test_windows_cover_period():
    assert p2.windows(1001) == [(0, 253), (252, 505), (504, 757), (756, 1001)]
    assert p2.windows(260) == [(0, 253)]  # Rest < 63 Tage zählt nicht


# ---------------------------------------------------------------- Ablauf (künstliches Universum) und Sperre
def fake_universe(variant, start="2000-01-03", n=252 * 9):
    from quantdesk.etf_data import CORE_KEYS, ChainedSeries, EtfUniverse, Segment

    days = business_days(start, n)
    if variant == "B":
        days = days[252 * 2:]  # echte ETFs beginnen später
    series = {}
    for j, k in enumerate(CORE_KEYS):
        b = series_for(days, j)
        series[k] = ChainedSeries(k, k, b, [Segment(k, b[0].day, b[-1].day)])
    return EtfUniverse(series, [(days[0], 0.02)], [(days[0], 0.9)], [(days[0], 0.005)], variant, "snapshot-test")


def series_for(days, j):
    return series(days, 0.0001 * (j % 5) - 0.0001, vol=0.004 + 0.002 * (j % 3), seed=10 + j)


def setup_registration(tmp_path, status="BESTÄTIGT von Tim am 10.10.2026", mode="effektiv"):
    import shutil

    from quantdesk import trials

    root = tmp_path / "anmeldungen"
    root.mkdir()
    (root / "D.md").write_text(f"# D\n\n**Status:** {status}\n**Zählweise N:** {mode}\n", encoding="utf-8")
    reg = tmp_path / "registry.md"
    shutil.copy(trials.REGISTRY, reg)
    return str(root), str(reg)


def test_run_all_variants_on_fake_universe(tmp_path, monkeypatch, capsys):
    import research_etf
    from quantdesk import phase2_run

    root, reg = setup_registration(tmp_path)
    monkeypatch.setitem(phase2_run.MAIN_START, "A", "2001-01-02")
    monkeypatch.setitem(phase2_run.MAIN_START, "C", "2001-01-02")
    monkeypatch.setattr(phase2_run, "SIDE_START", "2001-01-02")
    universes = {v: fake_universe(v) for v in ("A", "B", "C")}
    evals, n, mode = phase2_run.run("D", universes, root, reg)
    assert (n, mode) == (12, "effektiv")
    assert [e.label[0] for e in evals] == ["A", "B", "C", "N"]
    b = evals[1]
    assert b.start == universes["B"].full_start(warmup=253)
    assert set(b.results) == {"D1 Trend lang (12 M)", "D2 Trend kurz+lang (2 M + 12 M)", "D3 Trend kurz+lang, beste 3",
                              "SPY halten (Massstab)", "60/40 SPY/IEF", "G Risikoparität (1/Vola, 6 M)",
                              "Alle Klassen gleich gewichtet"}
    v = b.verdicts[("D1 Trend lang (12 M)", "CHF")]
    assert v.deflated.n_trials == 12 and v.windows_total >= 5
    ok, why = phase2_run.final(evals, "D1 Trend lang (12 M)")
    assert isinstance(ok, bool) and why
    for ev in evals:
        research_etf.print_evaluation(ev)
    out = capsys.readouterr().out
    assert "Sicht 2" in out and "Korrelation" in out and "K5" in out


def test_run_refuses_without_confirmation(tmp_path):
    from quantdesk import phase2_run

    root, reg = setup_registration(tmp_path, status="ENTWURF – wartet auf Tim")
    with pytest.raises(PermissionError, match="ENTWURF"):
        phase2_run.run("D", {v: fake_universe(v) for v in ("A", "B", "C")}, root, reg)
    other = tmp_path / "ohne-zaehlweise"
    other.mkdir()
    root2, reg2 = setup_registration(other, mode="(offen)")
    with pytest.raises(PermissionError, match="Zählweise"):
        phase2_run.run("D", {v: fake_universe(v) for v in ("A", "B", "C")}, root2, reg2)


def test_cli_lauf_blocked_before_loading_data(tmp_path, capsys):
    import research_etf

    root, reg = setup_registration(tmp_path, status="ENTWURF")
    code = research_etf.main(["--root", str(tmp_path / "keine-daten"), "lauf", "D", "--anmeldungen", root,
                              "--register", reg])
    assert code == 1 and "Gesperrt" in capsys.readouterr().err  # nicht "kein Datenstand": Sperre kommt zuerst

