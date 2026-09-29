"""Strategie-Vergleich auf ~50 Aktien: Grid/DCA (Video, mit Trendfilter, im Training optimiert),
Momentum-Rotation, Kaufen und Halten und ein ETF – getrennt nach Training und Test.

Beispiele:
    python research.py                         # Universum aus data/universe.csv, ETF SPY, 10 Jahre
    python research.py --benchmark QQQ --years 8
    python research.py --no-sweep              # ohne Parameter-Suche (schneller)
"""
from __future__ import annotations

import argparse
import sys

import requests

from quantdesk.backtest import Costs
from quantdesk.history import load_history
from quantdesk.research import WARMUP, Row, best_grid_on, calendar, load_universe, periods, study

DISCLAIMER = "Backtests zeigen die Vergangenheit, nicht die Zukunft. Keine Anlageberatung."


def print_rows(title: str, rows: list[Row]) -> None:
    print(f"\n{title}")
    header = (f"{'Strategie':58}{'Rendite p.a.':>13}{'Sharpe':>8}{'max DD':>9}{'Ø investiert':>14}"
              f"{'p.a. / Ø inv.':>18}{'Trades':>8}")
    print(header)
    print("-" * len(header))
    for r in rows:
        p = r.perf
        print(f"{r.name[:57]:58}{p.cagr:>13.1%}{p.sharpe:>8.2f}{p.max_drawdown:>9.1%}{p.avg_invested:>14.0%}"
              f"{p.return_on_invested:>18.1%}{r.trades:>8}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", default="data/universe.csv", help="CSV mit Spalte 'symbol'")
    ap.add_argument("--benchmark", default="SPY", help="ETF zum Vergleich (Standard SPY)")
    ap.add_argument("--years", type=int, default=10, help="Jahre Backtest (plus 1 Jahr Vorlauf)")
    ap.add_argument("--order-usd", type=float, default=1000.0, help="Grid: Betrag pro Order in $")
    ap.add_argument("--fee", type=float, default=1.0, help="Kommission pro Order in $")
    ap.add_argument("--no-sweep", action="store_true", help="keine Grid-Parameter-Suche im Training")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    costs = Costs(args.order_usd, args.fee)
    symbols = load_universe(args.universe)
    data, missing = {}, []
    for n, sym in enumerate(symbols, 1):
        print(f"\rLade Kurse {n}/{len(symbols)} {sym:6}", end="", file=sys.stderr, flush=True)
        try:
            data[sym] = load_history(sym, args.years + 1)
        except (requests.RequestException, ValueError):
            missing.append(sym)
    try:
        benchmark = load_history(args.benchmark, args.years + 1)
    except (requests.RequestException, ValueError) as e:
        print(f"\nETF {args.benchmark} nicht ladbar: {e}", file=sys.stderr)
        return 1
    print(file=sys.stderr)
    if missing:
        print(f"Ohne Kursdaten (übersprungen): {', '.join(missing)}")
    short = [s for s, bars in data.items() if len(bars) < WARMUP + 20]
    print(f"{len(data)} Aktien geladen" + (f", davon mit kurzer Historie (steigen später ein): {', '.join(short)}" if short else ""))

    tuned = None
    if not args.no_sweep:
        cal = calendar(list(data.values()) + [benchmark])
        train = periods(cal)[0]
        tuned = best_grid_on(
            data, costs, train, cal,
            progress=lambda n, total: print(f"\rGrid-Parameter im Training: {n}/{total}", end="", file=sys.stderr, flush=True),
        )
        print(file=sys.stderr)
        print(f"Beste Grid-Parameter im TRAINING (nach Sharpe): {tuned.label()}")

    for title, rows in study(data, benchmark, args.benchmark.upper(), costs, tuned).items():
        print_rows(title, rows)

    print("\nSo liest du die Tabelle:")
    print("- Rendite p.a. und max DD beziehen sich aufs ganze Budget (inkl. nicht investiertem Cash).")
    print("- 'p.a. / Ø inv.' = Rendite p.a. geteilt durch den Ø investierten Anteil (≈ Rendite, wenn immer voll investiert).")
    print("- Sharpe = Rendite pro Einheit Schwankung (höher = besser). Entscheidend ist der TEST-Zeitraum.")
    print("- Universum = Firmen, die es heute noch gibt (Survivorship-Bias): Kaufstrategien sehen zu gut aus.")
    print(f"\n{DISCLAIMER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
