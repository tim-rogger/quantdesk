"""Forschungsphase 2 auf dem ETF-Universum (ohne Survivorship-Bias, lange Historie).

Jeder Lauf arbeitet mit einem eingefrorenen Datenstand (research/data/snapshot-*/) und nennt ihn im Bericht.

Beispiele:
    python research_etf.py snapshot --neu                      # neuen Datenstand anlegen (nie überschreiben)
    python research_etf.py snapshot --liste                    # vorhandene Datenstände
    python research_etf.py snapshot --pruefe                   # Prüfsummen des neusten Datenstands kontrollieren
    python research_etf.py snapshot --vergleiche ALT NEU       # rückwirkende Änderungen zwischen zwei Ständen
    python research_etf.py daten                               # Datenbasis (neuster Datenstand, Fassung A)
    python research_etf.py daten --snapshot snapshot-2026-10-09 --fassung B
    python research_etf.py lauf D                              # nur mit bestätigter Anmeldung (research/anmeldungen/D.md)
"""
from __future__ import annotations

import argparse
import sys

from quantdesk import phase2_run, trials
from quantdesk.phase2 import Market, measure
from quantdesk import snapshot as snap_mod
from quantdesk.etf_data import (
    ASSETS,
    CHF_RATE_NOTE,
    CORE_KEYS,
    FX_SERIES,
    RATE_SYMBOL,
    VARIANTS,
    available,
    coverage_rows,
    from_snapshot,
    required_series,
)

DISCLAIMER = "Backtests zeigen die Vergangenheit, nicht die Zukunft. Keine Anlageberatung."
WARMUP = 252


def cmd_snapshot(args) -> int:
    root = args.root
    if args.neu:
        def progress(i, n, sid):
            print(f"\rLade {i}/{n} {sid.key:28}", end="", file=sys.stderr, flush=True)
        path = snap_mod.create(required_series(), root, progress=progress)
        print(file=sys.stderr)
        s = snap_mod.Snapshot(path)
        print(f"Neuer Datenstand: {s.name} ({len(s.keys)} Reihen) in {path}")
        print("Die Rohdateien (*.raw.gz) bleiben lokal; MANIFEST.json gehört ins Git.")
        return 0
    if args.liste:
        names = snap_mod.list_snapshots(root)
        for name in names:
            s = snap_mod.Snapshot(f"{root}/{name}")
            print(f"{name}  abgerufen {s.created}  {len(s.keys)} Reihen")
        if not names:
            print("Kein Datenstand vorhanden.")
        return 0
    if args.pruefe is not None:
        s = snap_mod.open_snapshot(args.pruefe or None, root)
        problems = s.verify()
        print(s.header())
        print("Prüfsummen: alle in Ordnung." if not problems else "\n".join(problems))
        return 0 if not problems else 1
    if args.vergleiche:
        old, new = (snap_mod.open_snapshot(n, root) for n in args.vergleiche)
        diffs = snap_mod.compare(old, new)
        print(f"Vergleich {old.name} → {new.name}\n")
        print(f"{'Reihe':30}{'Status':10}{'Tage gem.':>10}{'rückw. geändert':>17}{'max Δ':>10}"
              f"{'Δ Rendite p.a.':>16}{'neue Tage':>11}{'fehlend':>9}")
        print("-" * 113)
        for d in diffs:
            if d.status in ("neu", "entfernt", "gleich"):
                print(f"{d.key:30}{d.status:10}")
                continue
            print(f"{d.key:30}{d.status:10}{d.common_days:>10}{d.changed_days:>17}{d.max_change:>10.2e}"
                  f"{d.cagr_change * 100:>15.3f}%{d.added_days:>11}{d.missing_days:>9}")
        retro = [d.key for d in diffs if d.retroactive]
        print("\nRückwirkend geändert: " + (", ".join(retro) if retro else "nichts – nur neue Tage angehängt."))
        print("(Yahoo: verglichen werden Tagesrenditen, nicht Kursniveaus – eine Neuskalierung von adjclose "
              "nach einer Dividende ist keine Änderung.)")
        return 0
    print("Bitte --neu, --liste, --pruefe oder --vergleiche angeben.", file=sys.stderr)
    return 2


def cmd_daten(args) -> int:
    snap = snap_mod.open_snapshot(args.snapshot, args.root)
    u = from_snapshot(snap, args.fassung)
    rows = coverage_rows(u)
    if args.csv:
        print(f"# {u.header()}")
        print("etf;anlageklasse;daten_ab;etf_ab;verkettung;ueberlappung;begruendung;warnungen")
        for r in rows:
            if "error" in r:
                print(f"{r['key']};{r['name']};FEHLER;;;;{r['error']};")
            else:
                ov = r["overlap"].text() if r["overlap"] else ""
                print(f"{r['key']};{r['name']};{r['start']};{r['etf_start']};{r['chain']};{ov};{r['note']};"
                      f"{len(r['warnings'])}")
        return 0
    core = {a.key for a in ASSETS}
    print(f"DATENBASIS ETF-UNIVERSUM – {u.header()}")
    print("(Yahoo adjclose = inkl. Ausschüttungen; FRED)\n")
    print(f"{'ETF':5} {'Anlageklasse':32} {'Daten ab':11} {'ETF ab':11} Verkettung")
    print("-" * 110)
    for r in rows:
        if "error" in r:
            print(f"{r['key']:5} {r['name'][:32]:32} FEHLER: {r['error']}")
            continue
        mark = "" if r["key"] in core else "  (nur E/F)"
        print(f"{r['key']:5} {r['name'][:32]:32} {r['start']:11} {r['etf_start']:11} {r['chain']}{mark}")
    print("\nErsatzreihen gegen ETF (gemessen auf diesem Datenstand):")
    for r in rows:
        if "error" in r:
            continue
        ov = r["overlap"].text() if r["overlap"] else "–"
        print(f"  {r['key']:5} {ov}. {r['note']}")
    print(f"\nCash-Zins: {RATE_SYMBOL} (Yahoo) ab {u.rates[0][0]} | USD/CHF: FRED {FX_SERIES} ab {u.fx[0][0]}")
    print(f"{CHF_RATE_NOTE}, ab {u.chf_rates[0][0]}")

    print(f"\nAnlageklassen des Trend-Universums mit ≥ 1 Jahr Historie (Vorlauf {WARMUP} Tage), jeweils Anfang Jahr:")
    first_year = min(int(s.start[:4]) for k, s in u.series.items() if k in core)
    last_year = int(max(s.bars[-1].day for s in u.series.values())[:4])
    for year in range(first_year, last_year + 1):
        keys = available(u, f"{year}-01-15", CORE_KEYS, WARMUP)
        print(f"  {year}: {len(keys)}/{len(CORE_KEYS)}  {' '.join(keys)}")
    print(f"Alle {len(CORE_KEYS)} Klassen vorhanden ab {u.full_start()}, mit 1 Jahr Vorlauf ab {u.full_start(warmup=WARMUP)}.")

    warned = [(r["key"], r["warnings"]) for r in rows if r.get("warnings")]
    print("\nDatenwarnungen (Tagesbewegung > 25 % oder Lücke > 10 Tage) – gemeldet, nicht verändert:")
    if not warned:
        print("  keine")
    for key, ws in warned:
        print(f"  {key}: " + "; ".join(ws[:6]) + (f" … (+{len(ws) - 6})" if len(ws) > 6 else ""))
    if u.missing:
        print(f"\nNicht geladen: {u.missing}")
    print("\nHinweis: Die Aktien-Studie aus Phase 1 (data/universe.csv) ist MIT Survivorship-Bias "
          "(nur heute noch existierende Firmen) und bleibt so gekennzeichnet.")
    print(f"\n{DISCLAIMER}")
    return 0


def _m(ok: bool) -> str:
    return "✓" if ok else "✗"


def print_evaluation(ev, name_width: int = 40) -> None:
    print(f"\n=== Fassung {ev.label}: {ev.start} – {ev.end}, {ev.verdicts[next(iter(ev.verdicts))].windows_total} Testjahre")
    for cur in phase2_run.CURRENCIES:
        print(f"\nSicht 1 (risikobereinigt), {cur} – K1 Sharpe ≥ SPY+0.2 · K2 max DD · K3 ≥ 2/3 Jahre · K4 > SPY+Cash "
              f"gl. Inv. · K5 DSR ≥ 0.95")
        head = (f"{'Strategie':{name_width}}{'p.a.':>7}{'Sharpe':>8}{'max DD':>8}{'Ø inv.':>7}{'Mix':>7}"
                f"{'Jahre':>7}{'DSR':>6}{'K1':>4}{'K2':>4}{'K3':>4}{'K4':>4}{'K5':>4}  Ergebnis")
        print(head)
        print("-" * len(head))
        for b in ev.benchmarks:
            p = measure(ev.results[b], Market(ev.universe.rates, ev.universe.fx, ev.universe.chf_rates), cur)
            print(f"{b[:name_width - 1]:{name_width}}{p.cagr:>7.1%}{p.sharpe:>8.2f}{p.max_drawdown:>8.1%}"
                  f"{p.avg_invested:>7.0%}{'':>7}{'':>7}{'':>6}{'':>20}  Vergleich")
        for c in ev.candidates:
            v = ev.verdicts[(c, cur)]
            p = v.perf
            print(f"{c[:name_width - 1]:{name_width}}{p.cagr:>7.1%}{p.sharpe:>8.2f}{p.max_drawdown:>8.1%}"
                  f"{p.avg_invested:>7.0%}{v.mix.cagr:>7.1%}{f'{v.windows_better}/{v.windows_total}':>7}"
                  f"{v.deflated.dsr:>6.2f}{_m(v.k1):>4}{_m(v.k2):>4}{_m(v.k3):>4}{_m(v.k4):>4}{_m(v.k5):>4}  "
                  f"{'BESTANDEN' if v.passed else 'durchgefallen'}")
    print("\nSicht 2 (risiko-normiert auf SPY-Schwankung, USD) – KENNZAHL, keine Empfehlung, nichts wird gehebelt")
    head = (f"{'Strategie':{name_width}}{'Hebel':>7}{'p.a.':>7}{'SPY':>7}{'max DD':>8}{'SPY DD':>8}"
            f"{'schl. Monat':>12}{'Finanz. p.a.':>13}{'R1':>4}{'R2':>4}")
    print(head)
    print("-" * len(head))
    for c in ev.candidates:
        n = ev.normalized[c]
        print(f"{c[:name_width - 1]:{name_width}}{n.leverage:>7.2f}{n.cagr:>7.1%}{n.spy_cagr:>7.1%}"
              f"{n.max_drawdown:>8.1%}{n.spy_max_drawdown:>8.1%}{n.worst_month:>12.1%}{n.financing_cost:>13.1%}"
              f"{_m(n.r1):>4}{_m(n.r2):>4}")
    names = ev.candidates + ev.benchmarks
    corr = ev.correlations()
    print("\nKorrelation der Monatsrenditen")
    print(f"{'':{name_width}}" + "".join(f"{i + 1:>6}" for i in range(len(names))))
    for i, a in enumerate(names):
        print(f"{f'{i + 1} {a}'[:name_width - 1]:{name_width}}" + "".join(f"{corr[(a, b)]:>6.2f}" for b in names))


def cmd_lauf(args) -> int:
    name = args.kandidat
    trials.require_confirmed(name, args.anmeldungen)  # vor dem Laden: ohne Bestätigung nichts rechnen
    snap = snap_mod.open_snapshot(args.snapshot, args.root)
    universes = {v: from_snapshot(snap, v) for v in ("A", "B", "C")}
    evals, n, mode = phase2_run.run(name, universes, args.anmeldungen, args.register)
    print(f"KANDIDAT {name} – {snap.header()}")
    print(f"Mehrfachtest: N = {n} Versuche (Zählweise {mode}, research/registry.md). Kosten: "
          f"{phase2_run.COSTS.commission:.0f} $/Order + {phase2_run.COSTS.slippage_bps:.0f} Bp. Schlupf. "
          f"Kapital {phase2_run.CAPITAL:,.0f} $.".replace(",", "'"))
    for ev in evals:
        print_evaluation(ev)
    print("\nURTEIL (Sicht 1, USD und CHF, A und B – bei Abweichung gilt B):")
    for c in evals[0].candidates:
        ok, why = phase2_run.final(evals, c)
        s2 = evals[1].normalized[c].passed
        print(f"  {c}: {'BESTANDEN' if ok else 'durchgefallen'} ({why}); Sicht 2 in B: "
              f"{'bestanden' if s2 else 'durchgefallen'}")
    print(f"\n{DISCLAIMER}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=snap_mod.SNAPSHOT_ROOT, help="Ordner der Datenstände")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="Datenstände anlegen, auflisten, prüfen, vergleichen")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--neu", action="store_true")
    g.add_argument("--liste", action="store_true")
    g.add_argument("--pruefe", nargs="?", const="", metavar="NAME")
    g.add_argument("--vergleiche", nargs=2, metavar=("ALT", "NEU"))
    d = sub.add_parser("daten", help="Datenbasis prüfen und dokumentieren")
    d.add_argument("--snapshot", default=None, help="Name des Datenstands (Standard: neuster)")
    d.add_argument("--fassung", default="A", choices=sorted(VARIANTS))
    d.add_argument("--csv", action="store_true")
    r = sub.add_parser("lauf", help="angemeldeten Kandidaten auswerten (nur mit bestätigter Anmeldung)")
    r.add_argument("kandidat", choices=sorted(phase2_run.CANDIDATES))
    r.add_argument("--snapshot", default=None, help="Name des Datenstands (Standard: neuster)")
    r.add_argument("--anmeldungen", default=trials.REGISTRATIONS)
    r.add_argument("--register", default=trials.REGISTRY)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return {"snapshot": cmd_snapshot, "daten": cmd_daten, "lauf": cmd_lauf}[args.cmd](args)
    except PermissionError as e:
        print(f"Gesperrt: {e}", file=sys.stderr)
        return 1
    except snap_mod.SnapshotError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    sys.exit(main())
