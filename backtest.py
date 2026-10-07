"""Backtester für die Grid/DCA-Strategie auf einzelnen Aktien – immer im Vergleich mit Kaufen und Halten.

Beispiele:
    python backtest.py AAPL                                  # Video-Strategie: 5 Levels à 2 %, nie verkaufen
    python backtest.py AAPL MSFT NKE --drawdown 5 --tp 10
    python backtest.py AAPL NKE --trend 200 --trend-exit     # nur über dem 200-Tage-Schnitt handeln
    python backtest.py AAPL MSFT --tp 10 --sl 10 --split     # erste und zweite Hälfte getrennt

Für den Vergleich mehrerer Strategien auf ~50 Aktien (inkl. Parameter-Suche): python research.py
"""
from __future__ import annotations

import argparse
import sys

import requests

from quantdesk.backtest import Costs, GridParams, Result, halves, simulate, summarize
from quantdesk.history import load_history
from quantdesk.research import WARMUP
from quantdesk.strategy import MAX_LEVELS, ValidationError

DISCLAIMER = "Backtests zeigen die Vergangenheit, nicht die Zukunft. Keine Anlageberatung."


def print_table(title: str, results: list[Result]) -> None:
    print(f"\n{title}")
    header = (f"{'Symbol':7}{'Zeitraum':24}{'p.a. Grid':>10}{'p.a. Halten':>12}{'Sharpe G/H':>12}"
              f"{'max DD Grid':>12}{'max DD Halten':>14}{'Ø invest.':>10}{'p.a. / Ø inv.':>14}{'Zyklen':>7}")
    print(header)
    print("-" * len(header))
    for r in results:
        g, h = r.perf, r.hold_perf
        print(f"{r.symbol:7}{r.start + ' – ' + r.end:24}{g.cagr:>10.1%}{h.cagr:>12.1%}"
              f"{f'{g.sharpe:.2f} / {h.sharpe:.2f}':>12}{g.max_drawdown:>12.1%}{h.max_drawdown:>14.1%}"
              f"{g.avg_invested:>10.0%}{g.return_on_invested:>14.1%}{r.cycles:>7}")
    s = summarize(results)
    sharpe_wins = sum(r.perf.sharpe > r.hold_perf.sharpe for r in results)
    print(f"→ Mehr Gewinn als Halten: {s.wins} von {s.n} · bessere Sharpe als Halten: {sharpe_wins} von {s.n} · "
          f"mit Verlust: {s.losers}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="+", help="z.B. AAPL MSFT SPY")
    p.add_argument("--levels", type=int, default=5, help=f"Anzahl Levels (1–{MAX_LEVELS}, Standard 5)")
    p.add_argument("--drawdown", type=float, default=2.0, help="Abstand pro Level in %% (Standard 2)")
    p.add_argument("--tp", type=float, default=None, help="Take-Profit in %% über Ø-Einstand (Standard: nie verkaufen)")
    p.add_argument("--sl", type=float, default=None, help="Stop-Loss in %% unter dem letzten Level")
    p.add_argument("--trend", type=int, default=None, help="Trendfilter: nur über dem N-Tage-Schnitt kaufen (z.B. 200)")
    p.add_argument("--trend-exit", action="store_true", help="mit --trend: alles verkaufen, wenn der Kurs darunter fällt")
    p.add_argument("--no-restart", action="store_true", help="nach einem Verkauf nicht neu einsteigen")
    p.add_argument("--years", type=int, default=10, help="Jahre Backtest (plus 1 Jahr Vorlauf, Standard 10)")
    p.add_argument("--order-usd", type=float, default=1000.0, help="Betrag pro Order in $ (Standard 1000)")
    p.add_argument("--fee", type=float, default=1.0, help="Kommission pro Order in $ (Standard 1)")
    p.add_argument("--split", action="store_true", help="zusätzlich erste und zweite Hälfte getrennt zeigen")
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
            trend_sma=args.trend,
            trend_exit=args.trend_exit,
        )
    except ValidationError as e:
        print(f"Ungültige Parameter: {e}", file=sys.stderr)
        return 2

    data = {}
    for sym in args.symbols:
        try:
            bars = load_history(sym, args.years + 1)
        except (requests.RequestException, ValueError) as e:
            print(f"{sym.upper()}: keine Kursdaten ({e}) – übersprungen", file=sys.stderr)
            continue
        start = max(WARMUP, len(bars) - args.years * 252)
        if len(bars) - start < 20:
            print(f"{sym.upper()}: zu kurze Historie – übersprungen", file=sys.stderr)
            continue
        data[sym.upper()] = (bars, start)
    if not data:
        return 1

    budget = (params.levels + 1) * costs.order_usd
    print(f"Budget pro Symbol: {budget:,.0f} $ ({params.levels + 1} Orders à {costs.order_usd:,.0f} $), "
          f"Kommission {costs.fee:g} $ pro Order. Das erste Jahr dient als Vorlauf.".replace(",", "'"))
    print_table(f"Strategie: {params.label()}", [simulate(b, params, costs, sym, start) for sym, (b, start) in data.items()])
    if args.split:
        parts = {sym: halves(len(b), start) for sym, (b, start) in data.items()}
        for n, label in ((0, "1. Hälfte"), (1, "2. Hälfte")):
            print_table(label, [simulate(data[sym][0], params, costs, sym, rng[n].start, rng[n].stop)
                                for sym, rng in parts.items()])
    print(f"\n{DISCLAIMER}")
    return 0


if __name__ == "__main__":
    # Windows-Konsole (cp1252) kennt nicht alle Zeichen (z.B. ✓, Ø) -> ersetzen statt abstürzen
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    sys.exit(main())
