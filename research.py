"""Strategie-Vergleich auf ~50 Aktien: Grid/DCA (Video, mit Trendfilter, im Training optimiert),
Momentum-Rotation, Kaufen und Halten und ein ETF – getrennt nach Training und Test.

Beispiele:
    python research.py                         # Universum aus data/universe.csv, ETF SPY, 10 Jahre
    python research.py --benchmark QQQ --years 8
    python research.py --no-sweep              # ohne Parameter-Suche (schneller)
    python research.py --walk-forward          # 3 Jahre lernen / 1 Jahr testen + Bestehen-Regel (ROADMAP.md)
"""
from __future__ import annotations

import argparse
import sys

import requests

from quantdesk.backtest import Costs
from quantdesk.history import load_history
from quantdesk.research import WARMUP, Row, best_grid_on, calendar, load_universe, periods, study
from quantdesk.walkforward import (
    CURRENCIES,
    ETF_SYMBOLS,
    FX_SYMBOL,
    RATE_SYMBOL,
    SHARPE_MARGIN,
    WINDOW_SHARE,
    Market,
    check,
    default_strategies,
    measure,
    walk_forward,
)

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


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def print_walk_forward(wins, benches, results, market: Market) -> None:
    labels = [f"{w.test.start[2:4]}/{w.test.end[2:4]}" for w in wins]
    spy = benches[0]
    print(f"\nWALK-FORWARD: 3 Jahre Training → 1 Jahr Test, {len(wins)} Testfenster "
          f"({wins[0].test.start} – {wins[-1].test.end})")
    print("Cash verzinst mit dem 3-Monats-T-Bill-Zins, Sharpe = Überrendite über diesem Zins, Handelskosten inklusive.")

    print("\nSharpe je Testjahr (USD)")
    header = f"{'Strategie':52}" + "".join(f"{lab:>8}" for lab in labels)
    print(header)
    print("-" * len(header))
    for res in benches + results:
        print(f"{res.name[:51]:52}" + "".join(f"{measure(c, market).sharpe:>8.2f}" for c in res.windows))

    verdicts = {}
    for cur in CURRENCIES:
        print(f"\nAlle Testjahre zusammengehängt, in {cur} – Bestehen-Regel: K1 Sharpe ≥ {spy.name.split()[3]} "
              f"+ {SHARPE_MARGIN}, K2 max DD nicht schlimmer, K3 in ≥ {WINDOW_SHARE:.0%} der Jahre besser, "
              "K4 besser als SPY+Cash mit gleichem Inv.-Grad")
        header = (f"{'Strategie':52}{'Rendite p.a.':>13}{'Sharpe':>8}{'max DD':>9}{'Ø inv.':>8}"
                  f"{'Mix gl. Inv.':>13}{'Jahre besser':>14}{'K1':>4}{'K2':>4}{'K3':>4}{'K4':>4}  Ergebnis")
        print(header)
        print("-" * len(header))
        for b in benches:
            p = measure(b.stitched, market, cur)
            role = "Massstab" if b is spy else "Vergleich"
            print(f"{b.name[:51]:52}{p.cagr:>13.1%}{p.sharpe:>8.2f}{p.max_drawdown:>9.1%}{p.avg_invested:>8.0%}"
                  f"{'–':>13}{'–':>14}{'':>16}  {role}")
        for res in results:
            v = check(res, spy, market, cur)
            verdicts[(res.name, cur)] = v
            p = v.perf
            print(f"{res.name[:51]:52}{p.cagr:>13.1%}{p.sharpe:>8.2f}{p.max_drawdown:>9.1%}{p.avg_invested:>8.0%}"
                  f"{v.mix.cagr:>13.1%}{f'{v.windows_better}/{len(wins)}':>14}"
                  f"{_mark(v.k1):>4}{_mark(v.k2):>4}{_mark(v.k3):>4}{_mark(v.k4):>4}  "
                  f"{'BESTANDEN' if v.passed else 'durchgefallen'}")

    sixty = next((b for b in benches if b.name.startswith("60/40")), None)
    if sixty is not None:
        s = measure(sixty.stitched, market)
        print(f"\nVergleich mit 60/40 SPY/AGG (USD): Rendite p.a. {s.cagr:.1%}, Sharpe {s.sharpe:.2f}, max DD {s.max_drawdown:.1%}")
        header = f"{'Strategie':52}{'Δ Rendite p.a.':>16}{'Δ Sharpe':>10}{'Δ max DD':>10}"
        print(header)
        print("-" * len(header))
        for res in results:
            p = verdicts[(res.name, "USD")].perf
            print(f"{res.name[:51]:52}{p.cagr - s.cagr:>+16.1%}{p.sharpe - s.sharpe:>+10.2f}{p.max_drawdown - s.max_drawdown:>+10.1%}")

    for res in results:
        if any(res.notes):
            print(f"\n{res.name} – gewählte Parameter pro Trainingsfenster:")
            for lab, note in zip(labels, res.notes):
                print(f"  Test {lab}: {note}")
    for cur in CURRENCIES:
        passed = [res.name for res in results if verdicts[(res.name, cur)].passed]
        print(f"\n→ {cur}: " + (f"Bestanden: {', '.join(passed)}" if passed else "Keine Strategie hat die Bestehen-Regel erfüllt."))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", default="data/universe.csv", help="CSV mit Spalte 'symbol'")
    ap.add_argument("--benchmark", default="SPY", help="ETF zum Vergleich (Standard SPY)")
    ap.add_argument("--years", type=int, default=10, help="Jahre Backtest (plus 1 Jahr Vorlauf)")
    ap.add_argument("--order-usd", type=float, default=1000.0, help="Grid: Betrag pro Order in $")
    ap.add_argument("--fee", type=float, default=1.0, help="Kommission pro Order in $")
    ap.add_argument("--no-sweep", action="store_true", help="keine Grid-Parameter-Suche im Training")
    ap.add_argument("--walk-forward", action="store_true", help="Walk-forward-Test mit Bestehen-Regel (ROADMAP.md)")
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

    if args.walk_forward:
        def progress(n, total):
            print(f"\rGrid-Parameter im Trainingsfenster: {n}/{total}", end="", file=sys.stderr, flush=True)
        etfs, extra = {}, {}
        for sym in ETF_SYMBOLS + (RATE_SYMBOL, FX_SYMBOL):
            try:
                bars = load_history(sym, args.years + 1)
            except (requests.RequestException, ValueError):
                print(f"{sym}: keine Daten – ohne diesen Teil weiter", file=sys.stderr)
                continue
            (extra if sym in (RATE_SYMBOL, FX_SYMBOL) else etfs)[sym] = bars
        market = Market.from_bars(extra.get(RATE_SYMBOL), extra.get(FX_SYMBOL))
        if RATE_SYMBOL not in extra:
            print("Hinweis: kein T-Bill-Zins verfügbar – Cash wird nicht verzinst.")
        strategies = default_strategies(tune=not args.no_sweep, progress=progress, etfs=etfs)
        wins, benches, results = walk_forward(data, benchmark, args.benchmark.upper(), costs, strategies, etfs)
        print(file=sys.stderr)
        print_walk_forward(wins, benches, results, market)
        print(f"\n{DISCLAIMER}")
        return 0

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
