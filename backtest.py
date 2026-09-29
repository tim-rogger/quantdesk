"""Backtester für die Grid/DCA-Strategie – vergleicht immer mit Kaufen und Halten.

Beispiele:
    python backtest.py AAPL                                  # Video-Strategie: 5 Levels à 2 %, nie verkaufen
    python backtest.py AAPL MSFT NKE --levels 5 --drawdown 5 --tp 10
    python backtest.py AAPL MSFT --tp 10 --sl 10 --split     # erste und zweite Hälfte getrennt
    python backtest.py SPY AAPL MSFT NKE INTC --sweep        # Parameter suchen (Training) und ehrlich prüfen (Test)
"""
from __future__ import annotations

import argparse
import sys

import requests

from quantdesk.backtest import Costs, GridParams, Result, simulate, split, sweep
from quantdesk.history import load_history
from quantdesk.strategy import MAX_LEVELS, ValidationError

DISCLAIMER = "Backtests zeigen die Vergangenheit, nicht die Zukunft. Keine Anlageberatung."


def _money(x: float) -> str:
    return f"{x:,.0f}".replace(",", "'")


def print_table(title: str, results: list[Result]) -> None:
    print(f"\n{title}")
    header = (f"{'Symbol':7}{'Zeitraum':24}{'Strategie $':>12}{'p.a.':>7}{'Halten $':>12}{'p.a.':>7}"
              f"{'Differenz':>11}{'max Kapital':>12}{'max DD $':>10}{'Zyklen':>7}{'Käufe':>6}{'offen':>6}")
    print(header)
    print("-" * len(header))
    for r in results:
        print(f"{r.symbol:7}{r.start + ' – ' + r.end:24}{_money(r.pnl):>12}{r.cagr:>7.1%}{_money(r.hold_pnl):>12}"
              f"{r.hold_cagr:>7.1%}{_money(r.excess):>11}{_money(r.max_capital):>12}{_money(r.max_drawdown):>10}"
              f"{r.cycles:>7}{r.buys:>6}{'ja' if r.open_position else 'nein':>6}")
    if len(results) > 1:
        print("-" * len(header))
        print(f"{'Summe':31}{_money(sum(r.pnl for r in results)):>12}{'':>7}{_money(sum(r.hold_pnl for r in results)):>12}"
              f"{'':>7}{_money(sum(r.excess for r in results)):>11}")
    wins = sum(r.excess > 0 for r in results)
    losers = sum(r.pnl < 0 for r in results)
    print(f"→ Strategie schlägt Kaufen und Halten bei {wins} von {len(results)} Symbol(en); "
          f"{losers} Symbol(e) mit Verlust.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="+", help="z.B. AAPL MSFT SPY")
    p.add_argument("--levels", type=int, default=5, help=f"Anzahl Levels (1–{MAX_LEVELS}, Standard 5)")
    p.add_argument("--drawdown", type=float, default=2.0, help="Abstand pro Level in %% (Standard 2)")
    p.add_argument("--tp", type=float, default=None, help="Take-Profit in %% über Ø-Einstand (Standard: nie verkaufen)")
    p.add_argument("--sl", type=float, default=None, help="Stop-Loss in %% unter dem letzten Level")
    p.add_argument("--no-restart", action="store_true", help="nach einem Verkauf nicht neu einsteigen")
    p.add_argument("--years", type=int, default=10, help="Jahre Historie (Standard 10)")
    p.add_argument("--order-usd", type=float, default=1000.0, help="Betrag pro Order in $ (Standard 1000)")
    p.add_argument("--fee", type=float, default=1.0, help="Kommission pro Order in $ (Standard 1)")
    p.add_argument("--split", action="store_true", help="zusätzlich erste und zweite Hälfte getrennt zeigen")
    p.add_argument("--sweep", action="store_true", help="Parameter auf 1. Hälfte optimieren, auf 2. Hälfte testen")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    costs = Costs(args.order_usd, args.fee)
    try:
        params = GridParams(
            args.levels,
            args.drawdown / 100,
            args.tp / 100 if args.tp else None,
            args.sl / 100 if args.sl else None,
            restart=not args.no_restart,
        )
    except ValidationError as e:
        print(f"Ungültige Parameter: {e}", file=sys.stderr)
        return 2

    data = {}
    for sym in args.symbols:
        try:
            data[sym.upper()] = load_history(sym, args.years)
        except (requests.RequestException, ValueError) as e:
            print(f"{sym.upper()}: keine Kursdaten ({e}) – übersprungen", file=sys.stderr)
    if not data:
        return 1

    budget = (params.levels + 1) * costs.order_usd
    print(f"Budget pro Symbol: {_money(budget)} $ ({params.levels + 1} Orders à {_money(costs.order_usd)} $), "
          f"Kommission {costs.fee:g} $ pro Order.")

    if args.sweep:
        result = sweep(data, costs=costs)
        print(f"\nSweep: {len(result.ranking)} Parameter-Kombinationen auf der 1. Hälfte getestet.")
        print("Top 5 im Training (Summe Differenz zu Halten):")
        for excess, p in result.ranking[:5]:
            print(f"  {p.label():32} {_money(excess):>10} $")
        print_table(f"TRAINING (1. Hälfte) – beste Parameter: {result.best.label()}", result.train)
        print_table(f"TEST (2. Hälfte, nie gesehen) – dieselben Parameter: {result.best.label()}", result.test)
        print("\nEntscheidend ist die TEST-Tabelle. Sieht das Training viel besser aus als der Test, "
              "wurden die Parameter an die Vergangenheit angepasst (Overfitting).")
    else:
        print_table(f"Strategie: {params.label()}", [simulate(bars, params, costs, sym) for sym, bars in data.items()])
        if args.split:
            halves = {sym: split(bars) for sym, bars in data.items()}
            print_table("1. Hälfte", [simulate(a, params, costs, sym) for sym, (a, _) in halves.items()])
            print_table("2. Hälfte", [simulate(b, params, costs, sym) for sym, (_, b) in halves.items()])
    print(f"\n{DISCLAIMER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
