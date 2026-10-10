"""Forschungsphase 2, Teil 1: ETF-Datenbasis – Verkettung ohne Sprung, Segmente sichtbar, Fassungen A/B/C."""
import math

import pytest

import research
from quantdesk import etf_data as ed
from quantdesk.history import Bar, utc_day
from quantdesk.metrics import perf
from quantdesk.research import BIAS_LABEL, Row
from quantdesk.snapshot import SeriesId, parse_fred


def bars(days_closes):
    return [Bar(d, c, c, c, c) for d, c in days_closes]


def test_utc_day_before_1970():
    assert utc_day(0) == "1970-01-01"
    assert utc_day(-86400 * 365) == "1969-01-01"
    assert utc_day(315_532_800) == "1980-01-01"


def test_chain_uses_proxy_returns_before_etf_and_joins_without_jump():
    proxy = bars([("2000-01-03", 10.0), ("2000-01-04", 11.0), ("2000-01-05", 12.1), ("2000-01-06", 12.0)])
    etf = bars([("2000-01-05", 100.0), ("2000-01-06", 101.0)])
    out, segs = ed.chain(etf, proxy, "ETF", "PROXY")
    assert [b.day for b in out] == ["2000-01-03", "2000-01-04", "2000-01-05", "2000-01-06"]
    # Rendite vor dem ETF = Rendite der Ersatzreihe, Übergang ohne Sprung (Skalierung am ersten gemeinsamen Tag)
    assert out[1].close / out[0].close == pytest.approx(1.1)
    assert out[2].close / out[1].close == pytest.approx(1.1)
    assert out[2].close == 100.0 and out[3].close == 101.0
    assert segs == [ed.Segment("PROXY", "2000-01-03", "2000-01-04"), ed.Segment("ETF", "2000-01-05", "2000-01-06")]


def test_chain_without_older_proxy_keeps_etf_only():
    etf = bars([("2000-01-05", 100.0), ("2000-01-06", 101.0)])
    out, segs = ed.chain(etf, bars([("2000-01-05", 5.0)]), "ETF", "P")
    assert out == etf and [s.source for s in segs] == ["ETF"]
    out, segs = ed.chain(etf, None, "ETF", None)
    assert [s.source for s in segs] == ["ETF"]


def test_chain_needs_common_day():
    with pytest.raises(ValueError):
        ed.chain(bars([("2001-01-02", 1.0)]), bars([("2000-01-03", 1.0)]), "ETF", "P")


def test_blend_daily_rebalanced():
    a = bars([("d1", 100.0), ("d2", 110.0), ("d3", 110.0)])
    b = bars([("d1", 50.0), ("d2", 50.0), ("d3", 45.0)])
    out = ed.blend_bars({"A": a, "B": b}, {"A": 0.6, "B": 0.4})
    assert out[1].close == pytest.approx(100 * (1 + 0.6 * 0.10))
    assert out[2].close == pytest.approx(out[1].close * (1 + 0.4 * -0.10))
    with pytest.raises(ValueError):
        ed.blend_bars({"A": a}, {"A": 0.5})


def test_quality_warnings_report_but_do_not_change():
    b = bars([("2020-01-02", 100.0), ("2020-01-03", 140.0), ("2020-01-31", 141.0)])
    w = ed.quality_warnings(b)
    assert any("+40.0%" in x for x in w) and any("Lücke von 28 Tagen" in x for x in w)
    assert b[1].close == 140.0


def test_parse_fred_skips_missing():
    text = "observation_date,DEXSZUS\n1971-01-04,4.3180\n1971-01-05,.\n1971-01-06,4.3100\n"
    assert parse_fred(text, "DEXSZUS") == [("1971-01-04", 4.318), ("1971-01-06", 4.31)]
    with pytest.raises(ValueError):
        parse_fred("observation_date,X\n", "X")


def test_chained_rates_switch_at_start():
    old = [("1998-12-01", 0.01), ("1999-06-01", 0.012), ("1999-07-01", 0.5)]
    new = [("1999-07-01", 0.009), ("1999-08-01", 0.0095)]
    assert ed.chained_rates([(old, "1972-01-01"), (new, "1999-07-01")]) == [
        ("1998-12-01", 0.01), ("1999-06-01", 0.012), ("1999-07-01", 0.009), ("1999-08-01", 0.0095)]


# ---------------------------------------------------------------- Überlappung und Fassungen
def monthly(start_year, years, growth, offset=0):
    """Kurse am 1. jedes Monats mit konstantem Wachstum p.a."""
    out, v = [], 100.0
    for i in range(years * 12):
        y, m = start_year + i // 12, i % 12 + 1
        out.append((f"{y}-{m:02d}-01", v * (1 + growth) ** (i / 12) * (1 + 0.01 * ((i + offset) % 3 - 1))))
    return bars(out)


def test_overlap_stats_measure_drift():
    etf = monthly(2000, 10, 0.05)
    proxy = monthly(2000, 10, 0.06)
    ov = ed.overlap_stats(etf, proxy)
    assert ov.correlation == pytest.approx(1.0, abs=1e-6)
    assert ov.diff == pytest.approx(0.01, abs=0.002)
    assert ed.overlap_stats(etf[:5], proxy[:5]) is None  # zu kurz


def test_drift_shifts_annual_return():
    b = monthly(2000, 10, 0.0)
    shifted = ed.drift(b, -0.02)
    years = 9 + 11 / 12
    ratio = (shifted[-1].close / shifted[0].close) / (b[-1].close / b[0].close)
    assert ratio == pytest.approx(0.98 ** years, rel=2e-3)


def loader_for(proxy_growth=0.06):
    """Ersatzreihe 1990–2010 (6 % p.a.), ETF ab 2000 (5 % p.a.)."""
    def load(symbol):
        if symbol == "SPY":
            return [b for b in monthly(1990, 20, 0.05) if b.day >= "2000"]
        if symbol == "VFINX":
            return monthly(1990, 20, proxy_growth)
        if symbol == "^IRX":
            return bars([("1990-01-01", 5.0)])
        raise ValueError(symbol)
    return load


def fred(_sid):
    return [("1990-01-01", 1.5)]


def test_variant_a_b_c():
    specs = (ed.ASSETS[0],)
    a = ed.load_universe(loader_for(), fred, specs, "A", "snap-x").series["SPY"]
    b = ed.load_universe(loader_for(), fred, specs, "B", "snap-x").series["SPY"]
    c = ed.load_universe(loader_for(), fred, specs, "C", "snap-x").series["SPY"]
    assert a.start == "1990-01-01" and b.start == "2000-01-01" and c.start == "1990-01-01"
    assert [s.source for s in b.segments] == ["SPY"]
    assert a.overlap.diff == pytest.approx(0.01, abs=0.002) and a.correction == 0.0
    assert c.correction == pytest.approx(-0.0094, abs=0.002) and "korr." in c.segments[0].source
    # Fassung C: vor dem ETF wächst die korrigierte Reihe so schnell wie der ETF in der Überlappung
    pre = [x for x in c.bars if x.day < "2000"]
    g = (pre[-1].close / pre[0].close) ** (1 / (9 + 11 / 12)) - 1
    assert g == pytest.approx(0.05, abs=0.003)
    with pytest.raises(ValueError):
        ed.build_asset(specs[0], loader_for(), "X")


def test_universe_header_and_full_start():
    u = ed.load_universe(loader_for(), fred, (ed.ASSETS[0],), "B", "snapshot-2026-10-09")
    assert u.header() == "snapshot-2026-10-09 | Fassung B: nur echte ETF-Daten"
    assert u.full_start(("SPY",)) == "2000-01-01" and u.full_start(("SPY",), warmup=12) == "2001-01-01"
    assert u.full_start(("SPY", "EFA")) is None


def fake_loader(symbol):
    start = {"VFINX": "1980", "VEURX": "1990", "VPACX": "1990"}.get(symbol, "2000")
    days = [f"{y}-06-{d:02d}" for y in range(int(start), 2006) for d in (1, 2, 3)]
    if symbol in ("SPY", "EFA"):
        days = [d for d in days if d >= "2003"]
    return bars([(d, 100.0 + i) for i, d in enumerate(days)])


def test_load_universe_marks_every_segment_and_missing():
    specs = (ed.ASSETS[0], ed.ASSETS[1])  # SPY <- VFINX, EFA <- Mischung VEURX/VPACX
    u = ed.load_universe(fake_loader, fred, specs)
    spy, efa = u.series["SPY"], u.series["EFA"]
    assert [s.source for s in spy.segments] == ["VFINX", "SPY"] and spy.start.startswith("1980")
    assert spy.source_on("1990-06-02") == "VFINX" and spy.source_on("2004-06-02") == "SPY"
    assert [s.source for s in efa.segments] == ["VEURX+VPACX", "EFA"]
    assert u.rates[0][1] == pytest.approx(1.0) and u.fx == [("1990-01-01", 1.5)]
    rows = ed.coverage_rows(u, specs)
    assert rows[0]["chain"] == "VFINX (1980-06) → SPY (2003-06)" and rows[0]["etf_start"] == "2003-06-01"
    assert ed.available(u, "1985-01-01", ("SPY", "EFA"), warmup=3) == ["SPY"]


def test_required_series_cover_all_sources():
    keys = {s.key for s in ed.required_series()}
    for a in ed.ALL_ASSETS:
        assert f"yahoo:{a.etf.symbol}" in keys
        if a.proxy:
            assert all(f"yahoo:{s}" in keys for s in a.proxy.symbols)
    assert {"yahoo:^IRX", "fred:DEXSZUS", "fred:IRSTCI01CHM156N", "fred:IR3TIB01CHM156N"} <= keys
    assert SeriesId("yahoo", "GC=F").filename == "yahoo_GC_F.raw.gz"


def test_specs_are_documented_and_core_universe_complete():
    keys = [a.key for a in ed.ASSETS]
    assert keys == ["SPY", "EFA", "EEM", "IEF", "TLT", "LQD", "GLD", "DBC", "VNQ"]
    for a in ed.ALL_ASSETS:
        assert a.note and a.name and a.etf.kind == "ETF"
    assert all(a.proxy is not None for a in ed.ASSETS)  # jede Kern-Anlageklasse hat eine dokumentierte Verlängerung
    assert "^SPGSCI" not in {a.proxy.symbol for a in ed.ASSETS}  # Spot-Index ist nicht investierbar
    gold = next(a for a in ed.ASSETS if a.key == "GLD")
    assert gold.proxy.label == "GC=F (nur Kurs)"
    assert math.isclose(sum(w for _, w in ed.ASSETS[1].proxy.weights), 1.0)


def test_equity_study_reports_are_marked_survivorship_biased(capsys):
    p = perf([100.0, 101.0, 102.0])
    research.print_rows("TEST", [Row("x", p, 0, 1)])
    assert BIAS_LABEL in capsys.readouterr().out
