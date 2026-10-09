"""Bericht eines Kandidaten (Markdown), in der Reihenfolge, die Tim lesen will:
1. Urteil je Variante in einem Satz (woran es lag) – entscheidend ist B, wenn A und B abweichen
2. Kriterien K1–K5 mit Soll und Ist (wie knapp)
3. Mischungen SPY + Kandidat (Kennzahl, kein Kriterium)
4. Kennzahlen je Fassung A/B/C, Sicht 2, Korrelation, Nebenauswertung ab 1996
"""
from __future__ import annotations

import math

from quantdesk.etf_strategies import MIX_SPY_SHARES
from quantdesk.multitest import required_sharpe
from quantdesk.phase2 import DSR_LEVEL, SHARPE_MARGIN, WINDOW_SHARE, Verdict, correlation, measure
from quantdesk.phase2_run import CAPITAL, COSTS, CURRENCIES, Evaluation, final

DISCLAIMER = "Backtests zeigen die Vergangenheit, nicht die Zukunft. Keine Anlageberatung."


def _pct(x: float, signed: bool = False) -> str:
    return f"{x * 100:+.1f} %" if signed else f"{x * 100:.1f} %"


def _years_needed(v: Verdict) -> int:
    return math.ceil(WINDOW_SHARE * v.windows_total - 1e-9)


def criteria(v: Verdict) -> list[tuple[str, str, str, bool]]:
    """(Kriterium, Ist, Soll, erfüllt) für ein Urteil."""
    return [
        ("K1 Sharpe", f"{v.perf.sharpe:.2f}", f"≥ {v.spy.sharpe + SHARPE_MARGIN:.2f}", v.k1),
        ("K2 max DD", _pct(v.perf.max_drawdown), f"≥ {_pct(v.spy.max_drawdown)}", v.k2),
        ("K3 Jahre besser", f"{v.windows_better}/{v.windows_total}", f"≥ {_years_needed(v)}", v.k3),
        ("K4 Rendite p.a.", _pct(v.perf.cagr), f"> {_pct(v.mix.cagr)}", v.k4),
        ("K5 DSR", f"{v.deflated.dsr:.2f}", f"≥ {DSR_LEVEL:.2f}", v.k5),
    ]


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def verdict_sentence(evals: list[Evaluation], name: str) -> str:
    ok, why = final(evals, name)
    b = evals[1]
    misses = []
    for cur in CURRENCIES:
        for crit, ist, soll, good in criteria(b.verdicts[(name, cur)]):
            if not good:
                misses.append(f"{crit.split()[0]} {cur} ({ist} statt {soll})")
    head = f"**{name}: {'BESTANDEN' if ok else 'durchgefallen'}**"
    if ok:
        return f"{head} – alle fünf Kriterien in A und B, USD und CHF erfüllt ({why})."
    shown = "; ".join(misses[:4]) + (f"; +{len(misses) - 4} weitere" if len(misses) > 4 else "")
    return f"{head} ({why}). In B verfehlt: {shown}."


def report(name: str, evals: list[Evaluation], n: int, mode: str, snapshot_header: str) -> str:
    a, b = evals[0], evals[1]
    years_a = a.verdicts[(a.candidates[0], "USD")].deflated.t / 252
    years_b = b.verdicts[(b.candidates[0], "USD")].deflated.t / 252
    out = [f"# Kandidat {name} – Ergebnis", "",
           f"{snapshot_header}. Mehrfachtest: **N = {n}** (Zählweise {mode}, laufender Zähler, `research/registry.md`) "
           f"→ für K5 nötige Sharpe ≈ {required_sharpe(n, years_a):.2f} (A) bzw. {required_sharpe(n, years_b):.2f} (B). "
           f"Kosten {COSTS.commission:.0f} $/Order + {COSTS.slippage_bps:.0f} Bp. Schlupf, Kapital "
           f"{f'{CAPITAL:,.0f}'.replace(',', chr(39))} $, kein Hebel.", "",
           "## Kurz", ""]
    out += [f"- {verdict_sentence(evals, c)}" for c in a.candidates]
    out += ["", "Entscheidend ist Sicht 1 (K1–K5) in Fassung **B** (nur echte ETF-Daten), wenn A und B abweichen. "
            "Die Mischungen unten sind eine Kennzahl, **kein Bestehen**.", ""]

    out += ["## Kriterien – Soll und Ist", ""]
    cols = [(ev, cur) for ev in (a, b) for cur in CURRENCIES]
    for c in a.candidates:
        out += [f"**{c}**", "", "| Kriterium | " + " | ".join(f"{ev.label[0]} {cur}" for ev, cur in cols) + " |",
                "|---|" + "---|" * len(cols)]
        rows = {}
        for ev, cur in cols:
            for crit, ist, soll, good in criteria(ev.verdicts[(c, cur)]):
                rows.setdefault(crit, []).append(f"{ist} / {soll} {_mark(good)}")
        out += [f"| {crit} | " + " | ".join(cells) + " |" for crit, cells in rows.items()]
        out.append("")

    out += ["## Mischungen SPY + D (Kennzahl, kein Kriterium)", "",
            "Monatlich auf die Zielgewichte zurückgesetzt, gleiche Kosten. Δ = Unterschied zu 100 % SPY.", ""]
    for ev in (a, b):
        spy = {cur: measure(ev.spy, ev.market, cur) for cur in CURRENCIES}
        out += [f"**Fassung {ev.label}** ({ev.start} – {ev.end})", "",
                "| Mischung | p.a. USD | Sharpe USD | max DD USD | Δ p.a. | Δ Sharpe | Δ max DD | p.a. CHF | Sharpe CHF | "
                "max DD CHF | Δ Sharpe CHF |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        s, sc = spy["USD"], spy["CHF"]
        out.append(f"| 100 % SPY | {_pct(s.cagr)} | {s.sharpe:.2f} | {_pct(s.max_drawdown)} | | | | {_pct(sc.cagr)} | "
                   f"{sc.sharpe:.2f} | {_pct(sc.max_drawdown)} | |")
        for c in ev.candidates:
            for share in MIX_SPY_SHARES:
                res = ev.mixes[(c, share)]
                p, pc = measure(res, ev.market, "USD"), measure(res, ev.market, "CHF")
                out.append(f"| {res.name} | {_pct(p.cagr)} | {p.sharpe:.2f} | {_pct(p.max_drawdown)} | "
                           f"{_pct(p.cagr - s.cagr, True)} | {p.sharpe - s.sharpe:+.2f} | "
                           f"{_pct(p.max_drawdown - s.max_drawdown, True)} | {_pct(pc.cagr)} | {pc.sharpe:.2f} | "
                           f"{_pct(pc.max_drawdown)} | {pc.sharpe - sc.sharpe:+.2f} |")
        corr = ", ".join(f"{c.split()[0]} {correlation(ev.results[c], ev.spy):.2f}" for c in ev.candidates)
        out += ["", f"Korrelation der Monatsrenditen zu SPY: {corr}.", ""]

    out += ["## Kennzahlen je Fassung", ""]
    for ev in evals:
        first = ev.verdicts[(ev.candidates[0], "USD")]
        out += [f"### {ev.label}", "", f"{ev.start} – {ev.end}, {first.windows_total} Testjahre.", "",
                "| Strategie | p.a. USD | Sharpe USD | max DD USD | Ø inv. | p.a. CHF | Sharpe CHF | max DD CHF | "
                "Jahre besser | DSR | Sicht 1 |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        for name_ in ev.benchmarks:
            p, pc = measure(ev.results[name_], ev.market, "USD"), measure(ev.results[name_], ev.market, "CHF")
            out.append(f"| {name_} | {_pct(p.cagr)} | {p.sharpe:.2f} | {_pct(p.max_drawdown)} | {p.avg_invested:.0%} | "
                       f"{_pct(pc.cagr)} | {pc.sharpe:.2f} | {_pct(pc.max_drawdown)} | | | Vergleich |")
        for c in ev.candidates:
            v, vc = ev.verdicts[(c, "USD")], ev.verdicts[(c, "CHF")]
            out.append(f"| {c} | {_pct(v.perf.cagr)} | {v.perf.sharpe:.2f} | {_pct(v.perf.max_drawdown)} | "
                       f"{v.perf.avg_invested:.0%} | {_pct(vc.perf.cagr)} | {vc.perf.sharpe:.2f} | "
                       f"{_pct(vc.perf.max_drawdown)} | {v.windows_better}/{v.windows_total} | {v.deflated.dsr:.2f} | "
                       f"{'bestanden' if ev.passed(c) else 'durchgefallen'} |")
        out += ["", "Sicht 2 – risiko-normiert auf die SPY-Schwankung (USD, Kennzahl, nichts wird gehebelt):", "",
                "| Strategie | Hebel | p.a. | SPY p.a. | max DD | SPY max DD | schlimmster Monat | Finanzierung p.a. | R1 | R2 |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for c in ev.candidates:
            nz = ev.normalized[c]
            out.append(f"| {c} | {nz.leverage:.2f} | {_pct(nz.cagr)} | {_pct(nz.spy_cagr)} | {_pct(nz.max_drawdown)} | "
                       f"{_pct(nz.spy_max_drawdown)} | {_pct(nz.worst_month)} | {_pct(nz.financing_cost)} | "
                       f"{_mark(nz.r1)} | {_mark(nz.r2)} |")
        out.append("")
    names = b.candidates + b.benchmarks
    corr = b.correlations()
    out += ["### Korrelation der Monatsrenditen (Fassung B)", "",
            "| | " + " | ".join(str(i + 1) for i in range(len(names))) + " |", "|---|" + "---|" * len(names)]
    out += [f"| {i + 1} {x} | " + " | ".join(f"{corr[(x, y)]:.2f}" for y in names) + " |" for i, x in enumerate(names)]
    out += ["", DISCLAIMER, ""]
    return "\n".join(out)
