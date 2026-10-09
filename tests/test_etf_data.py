"""Forschungsphase 2, Teil 1: ETF-Datenbasis – Verkettung ohne Sprung, Segmente sichtbar, FRED, Qualität."""
from types import SimpleNamespace as NS

import pytest

import research
from quantdesk import etf_data as ed
from quantdesk.history import Bar, utc_day
from quantdesk.metrics import perf
from quantdesk.research import BIAS_LABEL, Row


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


def test_load_fred_parses_and_skips_missing(tmp_path):
    text = "observation_date,DEXSZUS\n1971-01-04,4.3180\n1971-01-05,.\n1971-01-06,4.3100\n"
    calls = []
    session = NS(get=lambda url, timeout: calls.append(url) or NS(text=text, raise_for_status=lambda: None))
    rows = ed.load_fred("DEXSZUS", session=session, cache_dir=str(tmp_path), now=0)
    assert rows == [("1971-01-04", 4.318), ("1971-01-06", 4.31)]
    assert ed.load_fred("DEXSZUS", session=session, cache_dir=str(tmp_path), now=0) == rows  # Cache
    assert len(calls) == 1 and "id=DEXSZUS" in calls[0]


def test_chained_rates_switch_at_start():
    old = [("1998-12-01", 0.01), ("1999-06-01", 0.012), ("1999-07-01", 0.5)]
    new = [("1999-07-01", 0.009), ("1999-08-01", 0.0095)]
    assert ed.chained_rates([(old, "1972-01-01"), (new, "1999-07-01")]) == [
        ("1998-12-01", 0.01), ("1999-06-01", 0.012), ("1999-07-01", 0.009), ("1999-08-01", 0.0095)]


def fake_loader(symbol):
    start = {"VFINX": "1980", "VEURX": "1990", "VPACX": "1990"}.get(symbol, "2000")
    days = [f"{y}-06-{d:02d}" for y in range(int(start), 2006) for d in (1, 2, 3)]
    if symbol in ("SPY", "EFA"):
        days = [d for d in days if d >= "2003"]
    return bars([(d, 100.0 + i) for i, d in enumerate(days)])


def test_load_universe_marks_every_segment_and_missing():
    specs = (ed.ASSETS[0], ed.ASSETS[1])  # SPY <- VFINX, EFA <- Mischung VEURX/VPACX
    u = ed.load_universe(specs, loader=fake_loader, fred=lambda sid: [("1990-01-01", 1.5)])
    spy, efa = u.series["SPY"], u.series["EFA"]
    assert [s.source for s in spy.segments] == ["VFINX", "SPY"] and spy.start.startswith("1980")
    assert spy.source_on("1990-06-02") == "VFINX" and spy.source_on("2004-06-02") == "SPY"
    assert [s.source for s in efa.segments] == ["VEURX+VPACX", "EFA"]
    assert u.rates[0][1] == pytest.approx(1.0) and u.fx == [("1990-01-01", 1.5)]
    rows = ed.coverage_rows(u, specs)
    assert rows[0]["chain"] == "VFINX (1980-06) → SPY (2003-06)" and rows[0]["etf_start"] == "2003-06-01"
    assert ed.available(u, "1985-01-01", ("SPY", "EFA"), warmup=3) == ["SPY"]


def test_specs_are_documented_and_core_universe_complete():
    keys = [a.key for a in ed.ASSETS]
    assert keys == ["SPY", "EFA", "EEM", "IEF", "TLT", "LQD", "GLD", "DBC", "VNQ"]
    for a in ed.ASSETS + ed.EXTRA_ASSETS:
        assert a.note and a.name and a.etf.kind == "ETF"
    assert all(a.proxy is not None for a in ed.ASSETS)  # jede Kern-Anlageklasse hat eine dokumentierte Verlängerung
    assert "^SPGSCI" not in {a.proxy.symbol for a in ed.ASSETS}  # Spot-Index ist nicht investierbar
    gold = next(a for a in ed.ASSETS if a.key == "GLD")
    assert gold.proxy.label == "GC=F (nur Kurs)"


def test_equity_study_reports_are_marked_survivorship_biased(capsys):
    p = perf([100.0, 101.0, 102.0])
    research.print_rows("TEST", [Row("x", p, 0, 1)])
    assert BIAS_LABEL in capsys.readouterr().out
