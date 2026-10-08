"""Migration des Altbestands (Zustand aus der Zeit vor Order-Register und Stückzahlen) – mit Trockenlauf und Bericht.

Schritte:
 1. Sicherung der Dateien (*.vor-migration-<zeit>)
 2. Journal bereinigen: unplausible Einträge (z.B. Menge 1.8e308 aus dem ersten Server-Lauf) entfernen
 3. Zustand bereinigen: unplausible Werte wurden beim Laden verworfen -> Sperre aufheben, Abgleich klärt den Rest
 4. Order-Register aus quantdesk.log, Zustand und Journal füllen (nur Orders des Bots)
 5. entry_qty und Level-Mengen aus Journal bzw. Register ergänzen
 6. Symbole ohne Handelsberechtigung (aus abgelehnten Orders im Log) sperren
 7. Abgleichslauf (keine neuen Orders): Fills nachbuchen, ältere Käufe aus der Position ableiten
Im Trockenlauf passiert alles auf Kopien in einem Temp-Ordner; die echten Dateien bleiben unverändert.
"""
from __future__ import annotations

import dataclasses
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field

from quantdesk import storage
from quantdesk.journal import Journal
from quantdesk.registry import BotOrder, OrderRegistry, not_tradable_from_log, orders_from_log
from quantdesk.strategy import FILLED

INVALID_STATE_PREFIX = "ungültige Werte im Zustand"


@dataclass
class MigrationReport:
    dry_run: bool
    backups: list[str] = field(default_factory=list)
    journal_removed: int = 0
    registry_added: int = 0
    registry_total: int = 0
    unblocked: list[str] = field(default_factory=list)
    entry_qty_added: list[str] = field(default_factory=list)
    level_qty_added: list[str] = field(default_factory=list)
    not_tradable: list[str] = field(default_factory=list)
    booked: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    journal_before: int = 0
    journal_after: int = 0

    def lines(self) -> list[str]:
        head = "TROCKENLAUF – nichts wurde geschrieben." if self.dry_run else "Migration ausgeführt."
        out = [head]
        if self.backups:
            out.append(f"Sicherungen: {', '.join(self.backups)}")
        out += [
            f"Journal: {self.journal_removed} unplausible Einträge entfernt; {self.journal_before} -> {self.journal_after} Einträge.",
            f"Order-Register: {self.registry_added} Orders neu, insgesamt {self.registry_total}.",
            f"Sperre aufgehoben (ungültige Werte verworfen): {', '.join(self.unblocked) or 'keine'}",
            f"entry_qty ergänzt: {', '.join(self.entry_qty_added) or 'keine'}",
            f"Level-Mengen ergänzt: {', '.join(self.level_qty_added) or 'keine'}",
            f"Ohne Handelsberechtigung gesperrt: {', '.join(self.not_tradable) or 'keine neuen'}",
        ]
        if self.booked:
            out.append("Nachgebucht:")
            out += ["  " + m for m in self.booked]
        out.append("Nicht zuordenbar – bitte prüfen:" if self.unresolved else "Nicht zuordenbar: nichts.")
        out += ["  - " + m for m in self.unresolved]
        return out


def _files(settings) -> list[str]:
    return [settings.data_file, settings.journal_file, settings.executions_file, settings.registry_file]


def migrate(settings, log_path: str, dry_run: bool = False, build=None) -> MigrationReport:
    """`build(settings, infer_missing=True)` liefert Services (Standard: quantdesk.app.build_services)."""
    if build is None:
        from quantdesk.app import build_services as build
    report = MigrationReport(dry_run)
    work = None
    if dry_run:
        work = tempfile.mkdtemp(prefix="quantdesk-migrate-")
        mapping = {}
        for name, path in (("data_file", settings.data_file), ("journal_file", settings.journal_file),
                           ("executions_file", settings.executions_file), ("registry_file", settings.registry_file)):
            target = os.path.join(work, os.path.basename(path))
            if os.path.exists(path):
                shutil.copy2(path, target)
            mapping[name] = target
        mapping["data_dir"] = os.path.join(work, "data")
        settings = dataclasses.replace(settings, **mapping)
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for path in _files(settings):
            if os.path.exists(path):
                shutil.copy2(path, f"{path}.vor-migration-{stamp}")
                report.backups.append(f"{os.path.basename(path)}.vor-migration-{stamp}")
    try:
        _run(settings, log_path, report, build)
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)
    return report


def _run(settings, log_path: str, report: MigrationReport, build) -> None:
    # 2. Journal bereinigen
    journal = Journal(settings.journal_file)
    valid = journal.read(include_simulated=True)
    report.journal_removed = len(journal.invalid)
    if journal.invalid:
        journal.rewrite(valid)

    # 4. Register
    registry = OrderRegistry(settings.registry_file)
    before = len(registry.all())
    log_lines = []
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8", errors="replace") as f:
            log_lines = f.readlines()
    for order in orders_from_log(log_lines):
        registry.add(order)
    systems = storage.load(settings.data_file)
    for s in systems.values():
        for lv in s.levels:
            if lv.order_id and not lv.order_id.startswith("DRY-") and s.order_usd and lv.price:
                registry.add(BotOrder(lv.order_id, s.symbol, "level", "BUY",
                                      float(lv.qty or round(s.order_usd / lv.price)), lv.level, lv.price))
    for f in valid:
        if f.kind in ("entry", "level", "exit") and f.order_id not in ("bestand", "") and not f.simulated:
            registry.add(BotOrder(f.order_id, f.symbol, f.kind, f.side, f.qty))
    reg = registry.all()
    report.registry_added, report.registry_total = len(reg) - before, len(reg)

    # 3./5./6. Zustand: Sperre wegen ungültiger Werte aufheben, Mengen ergänzen, Berechtigungen
    real_fills = [f for f in valid if not f.simulated]
    blocked_symbols = not_tradable_from_log(log_lines)
    for s in systems.values():
        if s.blocked and s.blocked.startswith(INVALID_STATE_PREFIX):
            s.blocked = None
            report.unblocked.append(s.symbol)
        if s.symbol in blocked_symbols and not s.not_tradable:
            s.not_tradable = True
            report.not_tradable.append(s.symbol)
        if s.entry_price is None or s.entry_qty is not None:
            continue
        mine = [f for f in real_fills if f.symbol == s.symbol]
        last_exit = max((f.ts for f in mine if f.side == "SELL"), default=-1.0)
        entries = [f for f in mine if f.kind in ("entry", "adopted") and f.ts > last_exit]
        if entries:
            s.entry_qty, s.entry_ts = entries[-1].qty, entries[-1].ts
            report.entry_qty_added.append(f"{s.symbol} {s.entry_qty:g} (Journal)")
        else:
            reg_entries = [o for o in reg.values() if o.symbol == s.symbol and o.kind == "entry"]
            if reg_entries:
                s.entry_qty = reg_entries[-1].qty
                report.entry_qty_added.append(f"{s.symbol} {s.entry_qty:g} (Log)")
        level_fills = {f.order_id: f for f in mine if f.kind == "level" and f.ts > last_exit}
        for lv in s.levels:
            if lv.status == FILLED and lv.qty is None:
                source = level_fills.get(lv.order_id) or reg.get(lv.order_id or "")
                if source is None:
                    continue
                lv.qty = source.qty
                report.level_qty_added.append(f"{s.symbol} L{lv.level} {lv.qty:g}")
                if lv.order_id not in level_fills:  # gefüllt laut Zustand, aber nie ins Journal gebucht
                    journal.record(s.symbol, "BUY", lv.qty, lv.price, "level", lv.order_id, False,
                                   when=getattr(source, "ts", None) or s.entry_ts, estimated=True,
                                   note="Migration: Level laut Zustand gefüllt, Preis = Limit")
                    report.booked.append(f"{s.symbol}: Level {lv.level} ({lv.qty:g} @ {lv.price}) aus dem Zustand "
                                         "ins Journal übernommen (Preis = Limit, geschätzt).")
    storage.save(settings.data_file, systems)

    # 7. Abgleichslauf
    services = build(settings, infer_missing=True)
    engine = services.engine
    report.journal_before = len(engine.journal.read())
    try:
        engine.run_once(decide=False)
    finally:
        services.broker.close()
    events = []
    while not engine.events.empty():
        events.append(engine.events.get())
    report.booked += [e.message for e in events
                     if any(k in e.message for k in ("nachgebucht", "gefüllt am", "abgeleitet", "Einstieg gefüllt"))]
    report.unresolved = [e.message for e in events if e.level == "error"]
    report.unresolved += [f"{s.symbol}: Einstieg ohne bekannte Stückzahl (entry_qty)"
                          for s in engine.systems.values() if s.entry_price is not None and s.entry_qty is None]
    report.journal_after = len(engine.journal.read())
