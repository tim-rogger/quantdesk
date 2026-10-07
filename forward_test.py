"""Vorwärtstest von Kandidat C auf dem Paper-Konto (siehe ROADMAP.md, Phase 3).

    python forward_test.py setup              # C-Systeme für data/universe.csv in equities.json anlegen (Status Off)
    python forward_test.py report             # Monatsreport: C live vs. Backtest vs. SPY vs. SPY/Cash-Mischung
    python forward_test.py report --include-dry   # Report auch mit simulierten DRY_RUN-Fills (zum Ausprobieren)
    python forward_test.py import-trades DATEI    # gespeicherte IBKR-Antwort von /iserver/account/trades ins Archiv
"""
from __future__ import annotations

import argparse
import json
import sys

import requests

from quantdesk.config import load_settings
from quantdesk.forward import (
    C_PARAMS,
    DEFAULT_ORDER_USD,
    TEST_MONTHS,
    build_report,
    c_systems,
    setup_systems,
)
from quantdesk.broker.ibkr import parse_executions
from quantdesk.executions import ExecutionArchive
from quantdesk.history import load_history
from quantdesk.journal import Journal
from quantdesk.research import load_universe
from quantdesk.walkforward import FX_SYMBOL, RATE_SYMBOL, Market

DISCLAIMER = "Paper-Trading über wenige Monate ist statistisch wenig aussagekräftig. Keine Anlageberatung."


def cmd_setup(args, settings) -> int:
    symbols = load_universe(args.universe)
    added, skipped = setup_systems(settings.data_file, symbols, args.order_usd)
    print(f"Kandidat C ({C_PARAMS.label()}, {args.order_usd:,.0f} $ pro Order) in {settings.data_file}:".replace(",", "'"))
    print(f"  neu angelegt (Status Off): {len(added)}" + (f" – {', '.join(added)}" if added else ""))
    if skipped:
        print(f"  schon vorhanden, nicht verändert: {', '.join(skipped)}")
    budget = len(c_systems(settings.data_file)) * (C_PARAMS.levels + 1) * args.order_usd
    print(f"  Budget des Vorwärtstests: {budget:,.0f} $ (so viel kann C maximal gleichzeitig investieren)".replace(",", "'"))
    print("\nNächste Schritte: Bot im DRY_RUN starten, alle C-Zeilen markieren (Shift-Klick) → Toggle.")
    print("Hält das Paper-Konto schon Aktien aus der Liste, übernimmt C diese Position. Vorher verkaufen, wenn")
    print("der Test sauber sein soll. Auf PAPER erst umstellen, wenn du den Start bestätigst.")
    return 0


def print_report(r) -> None:
    status = "ABGESCHLOSSEN" if r.finished else f"LÄUFT (Monat {r.months_done + 1} von {TEST_MONTHS})"
    print(f"\nVORWÄRTSTEST KANDIDAT C – {r.start} bis {r.end} ({r.trading_days} Handelstage) – {status}")
    for cur in ("USD", "CHF"):
        print(f"\nSeit Start, in {cur} (Cash verzinst, Kosten inklusive)")
        header = f"{'':38}{'Rendite':>10}{'p.a.':>9}{'Sharpe':>8}{'max DD':>9}{'Ø inv.':>8}"
        print(header)
        print("-" * len(header))
        for name, per in r.rows.items():
            p = per[cur]
            print(f"{name[:37]:38}{p.total_return:>10.1%}{p.cagr:>9.1%}{p.sharpe:>8.2f}{p.max_drawdown:>9.1%}{p.avg_invested:>8.0%}")
    print("\nMonatsrenditen (je 21 Handelstage, USD)")
    names = list(r.monthly)
    n = max(len(v) for v in r.monthly.values())
    header = f"{'':38}" + "".join(f"{'M' + str(i + 1):>8}" for i in range(n))
    print(header)
    print("-" * len(header))
    for name in names:
        print(f"{name[:37]:38}" + "".join(f"{x:>8.1%}" for x in r.monthly[name]))
    if r.estimated_fills:
        print(f"\nHinweis: {r.estimated_fills} Fill(s) mit geschätztem Tag (Ausführung bei IBKR nicht mehr abrufbar, "
              "Preis = Limitpreis) – siehe ROADMAP, Vorfälle.")
    print("\nBestehen-Kriterien (vorher festgelegt, siehe ROADMAP.md)")
    for c in r.checks:
        mark = "✓" if c.ok else "✗"
        print(f"  {c.code} {mark} {c.text}: {c.detail}")
    if not r.finished:
        print("\n→ Test läuft noch – Zwischenstand, noch kein Urteil.")
    else:
        print("\n→ " + ("BESTANDEN" if r.passed else "DURCHGEFALLEN") + " (alle Kriterien müssen erfüllt sein).")


def cmd_report(args, settings) -> int:
    systems = c_systems(settings.data_file)
    if not systems:
        print("Keine C-Systeme in equities.json – zuerst: python forward_test.py setup")
        return 1
    order_usd = systems[0].order_usd or DEFAULT_ORDER_USD
    symbols = {s.symbol for s in systems}
    fills = [f for f in Journal(args.journal or settings.journal_file).read(include_simulated=args.include_dry)
             if f.symbol in symbols]
    bars, missing = {}, []
    for sym in sorted(symbols):
        try:
            bars[sym] = load_history(sym, 2)
        except (requests.RequestException, ValueError):
            missing.append(sym)
    if missing:
        print(f"Ohne Kursdaten (übersprungen, zählen nicht zum Budget): {', '.join(missing)}")
    # Budget nur für Symbole mit Kursdaten – genau wie im Backtest desselben Zeitraums
    budget = len(bars) * (C_PARAMS.levels + 1) * order_usd
    fills = [f for f in fills if f.symbol in bars]
    try:
        spy = load_history("SPY", 2)
        rate = load_history(RATE_SYMBOL, 2)
        fx = load_history(FX_SYMBOL, 2)
    except (requests.RequestException, ValueError) as e:
        print(f"Vergleichsdaten nicht ladbar: {e}", file=sys.stderr)
        return 1
    try:
        report = build_report(fills, bars, spy, Market.from_bars(rate, fx), budget, order_usd, args.fee, args.start)
    except ValueError as e:
        print(e)
        return 1
    print_report(report)
    print(f"\n{DISCLAIMER}")
    return 0


def cmd_import_trades(args, settings) -> int:
    """Ausführungen aus einer gespeicherten IBKR-Antwort ins Archiv übernehmen (doppelte werden übersprungen)."""
    if not settings.ibkr_account_id:
        print("IBKR_ACCOUNT_ID fehlt in .env – ohne Konto-ID kann nicht gefiltert werden.")
        return 1
    with open(args.file, encoding="utf-8") as f:
        executions = parse_executions(json.load(f), settings.ibkr_account_id)
    archive = ExecutionArchive(settings.executions_file)
    before = len(archive.load())
    after = len(archive.merge(executions))
    print(f"{len(executions)} Ausführungen in {args.file}, davon {after - before} neu ins Archiv "
          f"{settings.executions_file} übernommen (jetzt {after}).")
    print("Beim nächsten Start bucht der Bot daraus fehlende Fills nach.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="C-Systeme anlegen (Status Off)")
    s.add_argument("--universe", default="data/universe.csv")
    s.add_argument("--order-usd", type=float, default=DEFAULT_ORDER_USD)
    r = sub.add_parser("report", help="Monatsreport")
    r.add_argument("--journal", default=None, help="Journal-Datei (Standard aus .env)")
    r.add_argument("--start", default=None, help="Starttag YYYY-MM-DD (Standard: erster Fill im Journal)")
    r.add_argument("--fee", type=float, default=1.0, help="Kommission pro Order in $")
    r.add_argument("--include-dry", action="store_true", help="simulierte DRY_RUN-Fills mitzählen")
    t = sub.add_parser("import-trades", help="gespeicherte IBKR-Ausführungen ins Archiv übernehmen")
    t.add_argument("file", help="JSON-Antwort von /iserver/account/trades")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    settings = load_settings()
    commands = {"setup": cmd_setup, "report": cmd_report, "import-trades": cmd_import_trades}
    return commands[args.cmd](args, settings)


if __name__ == "__main__":
    sys.exit(main())
