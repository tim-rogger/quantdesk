"""Forschungsphase 2 auf dem ETF-Universum (ohne Survivorship-Bias, lange Historie).

Beispiele:
    python research_etf.py daten            # Datenbasis: Quellen, Verkettung, Startdaten, Warnungen
    python research_etf.py daten --csv      # dieselbe Übersicht als CSV (für DATEN.md / Excel)
"""
from __future__ import annotations

import argparse
import sys

from quantdesk.etf_data import (
    ASSETS,
    CHF_RATE_NOTE,
    CORE_KEYS,
    FX_SERIES,
    RATE_SYMBOL,
    available,
    coverage_rows,
    load_universe,
)

DISCLAIMER = "Backtests zeigen die Vergangenheit, nicht die Zukunft. Keine Anlageberatung."
WARMUP = 252


def cmd_daten(args) -> int:
    u = load_universe()
    rows = coverage_rows(u)
    if args.csv:
        print("etf;anlageklasse;daten_ab;etf_ab;verkettung;begruendung;warnungen")
        for r in rows:
            if "error" in r:
                print(f"{r['key']};{r['name']};FEHLER;;;{r['error']};")
            else:
                print(f"{r['key']};{r['name']};{r['start']};{r['etf_start']};{r['chain']};{r['note']};"
                      f"{len(r['warnings'])}")
        return 0
    core = {a.key for a in ASSETS}
    print("DATENBASIS ETF-UNIVERSUM (Yahoo adjclose = inkl. Ausschüttungen, FRED)\n")
    header = f"{'ETF':5} {'Anlageklasse':32} {'Daten ab':11} {'ETF ab':11} Verkettung"
    print(header)
    print("-" * 110)
    for r in rows:
        if "error" in r:
            print(f"{r['key']:5} {r['name'][:32]:32} FEHLER: {r['error']}")
            continue
        mark = "" if r["key"] in core else "  (nur E/F)"
        print(f"{r['key']:5} {r['name'][:32]:32} {r['start']:11} {r['etf_start']:11} {r['chain']}{mark}")
    print("\nBegründung der Ersatzreihen (Überlappung mit dem ETF, Monatsrenditen):")
    for r in rows:
        if "note" in r:
            print(f"  {r['key']:5} {r['note']}")
    print(f"\nCash-Zins: {RATE_SYMBOL} (Yahoo) ab {u.rates[0][0]} | USD/CHF: FRED {FX_SERIES} ab {u.fx[0][0]}")
    print(f"{CHF_RATE_NOTE}, ab {u.chf_rates[0][0]}")

    print(f"\nAnlageklassen des Trend-Universums mit ≥ 1 Jahr Historie (Vorlauf {WARMUP} Tage), jeweils Anfang Jahr:")
    first_year = min(int(s.start[:4]) for k, s in u.series.items() if k in core)
    last_year = int(max(s.bars[-1].day for s in u.series.values())[:4])
    for year in range(first_year, last_year + 1):
        keys = available(u, f"{year}-01-15", CORE_KEYS, WARMUP)
        print(f"  {year}: {len(keys)}/{len(CORE_KEYS)}  {' '.join(keys)}")

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("daten", help="Datenbasis prüfen und dokumentieren")
    d.add_argument("--csv", action="store_true")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    return {"daten": cmd_daten}[args.cmd](args)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    sys.exit(main())
