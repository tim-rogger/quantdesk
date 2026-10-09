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
import os
import sys

from quantdesk import phase2_run, trials
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


RESULTS = "research/ergebnisse"


def _report_path(name: str, snapshot: str, root: str = RESULTS) -> str:
    """Neue Datei je Lauf – ein bestehender Bericht wird nie überschrieben."""
    os.makedirs(root, exist_ok=True)
    base = os.path.join(root, f"{name}-{snapshot}")
    path, k = base + ".md", 1
    while os.path.exists(path):
        k += 1
        path = f"{base}-{k}.md"
    return path


def _register_results(name: str, evals, n: int, snapshot: str, registry: str) -> list[int]:
    """Ergebnis je Variante in die angemeldete Registerzeile (z.B. 'D1: …') eintragen."""
    rows = trials.read(registry)
    done = []
    for c in evals[0].candidates:
        key = c.split()[0] + ":"
        row = next((t for t in rows if t.name.startswith(key) and t.phase == trials.ETF_PHASE), None)
        if row is None:
            raise KeyError(f"Register: keine angemeldete Zeile für {c} ({key})")
        ok, why = phase2_run.final(evals, c)
        vb = evals[1].verdicts[(c, "USD")]
        misses = [k for k, good in (("K1", vb.k1), ("K2", vb.k2), ("K3", vb.k3), ("K4", vb.k4), ("K5", vb.k5)) if not good]
        text = (f"{'BESTANDEN' if ok else 'durchgefallen'} ({why}); B USD: Sharpe {vb.perf.sharpe:.2f}, "
                f"max DD {vb.perf.max_drawdown:.1%}, DSR {vb.deflated.dsr:.2f}"
                + (f", verfehlt {' '.join(misses)}" if misses else "") + f"; N={n}, {snapshot}")
        trials.set_result(row.nr, "getestet", text, registry)
        done.append(row.nr)
    return done


def _code_version() -> str:
    """Git-Commit des verwendeten Codes (mit Hinweis, wenn uncommittete Änderungen da sind)."""
    import subprocess

    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "quantdesk", "research_etf.py"],
                               capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unbekannt"
    return head + (" (mit uncommitteten Änderungen!)" if dirty else "")


def cmd_lauf(args) -> int:
    from quantdesk.phase2_report import report

    name = args.kandidat
    trials.require_confirmed(name, args.anmeldungen)  # vor dem Laden: ohne Bestätigung nichts rechnen
    snap = snap_mod.open_snapshot(args.snapshot, args.root)
    universes = {v: from_snapshot(snap, v) for v in ("A", "B", "C")}
    evals, n, mode = phase2_run.run(name, universes, args.anmeldungen, args.register)
    text = report(name, evals, n, mode, f"{snap.header()}, Code {_code_version()}")
    print(text)
    if not args.ohne_eintrag:
        path = _report_path(name, snap.name, args.ergebnisse)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        nrs = _register_results(name, evals, n, snap.name, args.register)
        print(f"\nBericht: {path}. Register: Zeilen {', '.join(map(str, nrs))} aktualisiert.", file=sys.stderr)
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
    r.add_argument("--ergebnisse", default=RESULTS, help="Ordner für den Bericht")
    r.add_argument("--ohne-eintrag", action="store_true", help="nur anzeigen, keinen Bericht/Registereintrag schreiben")
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
