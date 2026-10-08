"""Headless-Tageslauf für den Server (ohne GUI). Vom Scheduler aufgerufen:

    python run_daily.py trade       # 10:00 New York: abgleichen, Trend prüfen, Einstiege/Stufen/Verkäufe, Snapshot, Push
    python run_daily.py reconcile   # 15:30 New York: nur Fills abgleichen und buchen – keine neuen Entscheide
    python run_daily.py trade --force   # auch ausserhalb eines Handelstags (zum Testen)

Exit-Codes: 0 = ok (auch: kein Handelstag, STOP aktiv), 1 = Fehler (z.B. Gateway nicht erreichbar), 2 = Konfiguration.
Kein Absturz-Loop: der Scheduler startet den nächsten Lauf erst zur nächsten geplanten Zeit.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import shutil
import sys
import time
import traceback

from quantdesk.app import build_services
from quantdesk.config import load_settings
from quantdesk.forward import load_report
from quantdesk.healthcheck import ping
from quantdesk.history import load_history
from quantdesk.notify import Notifier
from quantdesk.schedule import is_trading_day, ny_today
from quantdesk.status import (
    STOP_FILE,
    append_jsonl,
    money,
    pct,
    build_status,
    data_path,
    read_jsonl,
    save_json,
    stop_active,
    write_snapshot,
)

log = logging.getLogger("run_daily")

PASS_WAIT_SECONDS = 10  # nach Market-Orders kurz warten, damit Einstiege gefüllt und Stufen noch heute platziert werden
MAX_EXTRA_PASSES = 6
LOCK_STALE_SECONDS = 3600
DISK_ALARM = 0.85
RAM_ALARM = 0.90
EVENTS_KEEP = 500


def resource_warnings(path: str, meminfo: str = "/proc/meminfo") -> list[str]:
    """Speicher > 85 % oder RAM > 90 % belegt -> Warntexte (Linux; anderswo nur Speicher)."""
    out = []
    try:
        du = shutil.disk_usage(path)
        if du.total and du.used / du.total > DISK_ALARM:
            out.append(f"Speicher {du.used / du.total:.0%} belegt ({du.free / 1e9:.1f} GB frei).")
    except OSError:
        pass
    try:
        with open(meminfo, encoding="utf-8") as f:
            info = {line.split(":")[0]: float(line.split()[1]) for line in f if ":" in line}
        total, avail = info.get("MemTotal"), info.get("MemAvailable")
        if total and avail is not None and 1 - avail / total > RAM_ALARM:
            out.append(f"RAM {1 - avail / total:.0%} belegt.")
    except (OSError, ValueError, IndexError):
        pass
    return out


def append_events(path: str, events, keep: int = EVENTS_KEEP) -> None:
    """Ereignisse für den Verlauf im Dashboard (nur die letzten `keep`)."""
    rows = read_jsonl(path)
    rows += [{"ts": e.ts, "level": e.level, "symbol": e.symbol, "message": e.message}
             for e in events if e.level in ("order", "warn", "error")]
    rows = rows[-keep:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    os.replace(tmp, path)


def _pending(engine) -> bool:
    return any(s.entry_order_id or s.exit_order_id for s in engine.systems.values() if s.is_on and s.tradable)


def _acquire_lock(path: str) -> bool:
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < LOCK_STALE_SECONDS:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def _summary(mode: str, day: dt.date, events, status: dict | None) -> tuple[str, str]:
    fills = [e for e in events if e.level == "order" and ("gefüllt" in e.message or "nachgebucht" in e.message)]
    placed = [e for e in events if e.level == "order" and "platziert" in e.message]
    errors = [e for e in events if e.level == "error"]
    title = f"QuantDesk {day:%d.%m.} – {'Handel' if mode == 'trade' else 'Abgleich'}"
    lines = [f"{len(fills)} Ausführung(en), {len(placed)} neue Order(s), {len(errors)} Fehler."]
    if status:
        acc = status["c_account"]
        lines.append(f"C: {money(acc.get('value'))} (Budget {money(acc.get('budget'))}), "
                     f"investiert {money(acc.get('invested'))}.")
        rep = status.get("report")
        if rep:
            live = rep["rows"].get("C live (Paper)", {}).get("USD", {})
            spy = rep["rows"].get("SPY halten", {}).get("USD", {})
            if live and spy:
                lines.append(f"Seit Start: C {pct(live.get('total_return'))} | SPY {pct(spy.get('total_return'))}")
    return title, "\n".join(lines)


def run(mode: str, force: bool = False, push: bool = True, now: dt.datetime | None = None) -> int:
    try:
        settings = load_settings()
    except ValueError as e:
        print(f"Konfigurationsfehler: {e}", file=sys.stderr)
        return 2
    notifier = Notifier.from_settings(settings) if push else Notifier()
    day = ny_today(now)
    if not force and not is_trading_day(day):
        print(f"{day}: kein NYSE-Handelstag – nichts zu tun.")
        return 0

    lock = data_path(settings, "run.lock")
    if not _acquire_lock(lock):
        notifier.send("QuantDesk: Lauf übersprungen", "Ein anderer Lauf ist noch aktiv (run.lock).", "warn")
        return 1
    started = time.time()
    run_row = {"ts": started, "day": str(day), "mode": mode, "ok": False, "errors": [], "fills": 0, "orders": 0}
    services = None
    try:
        services = build_services(settings)
        engine = services.engine
        stopped = stop_active(settings)
        if stopped:
            engine.stop_all()
            notifier.send("QuantDesk: STOP-ALL aktiv",
                          f"Alle Systeme Off, kein Handel. Zum Aufheben: Datei {STOP_FILE} im Datenordner löschen.", "warn")
        else:
            engine.run_once(decide=(mode == "trade"))
            if mode == "trade":
                for _ in range(MAX_EXTRA_PASSES):
                    if not _pending(engine):
                        break
                    time.sleep(PASS_WAIT_SECONDS)
                    engine.run_once(decide=True)
        events = []
        while not engine.events.empty():
            events.append(engine.events.get())
        authed = True if stopped else engine.status_view.get("authenticated")
        # Broker-Hinweise (z.B. "nur verzögerte Kurse") einmal pro Lauf statt pro Symbol
        notices_fn = getattr(getattr(services.marketdata, "broker", None), "notices", None)
        for note in (notices_fn() if callable(notices_fn) else []):
            log.info(note)
            run_row.setdefault("notes", []).append(note)

        # Push: jede Ausführung/jeder Verkauf und jeder Fehler einzeln
        for e in events:
            if e.level == "error" or (e.level == "order" and ("gefüllt" in e.message or "nachgebucht" in e.message)):
                notifier.send(f"QuantDesk {e.symbol or ''}".strip(), e.message, e.level)

        # Snapshot + Status fürs Dashboard
        fills = engine.journal.read(include_simulated=settings.dry_run) if engine.journal else []
        try:
            net_liq = services.broker.get_net_liquidation() if authed else None
        except Exception:  # Nettowert ist nur Zusatzinfo
            net_liq = None
        report = None
        if mode == "trade":
            try:
                report, _missing = load_report(settings.data_file, engine.journal, load_history,
                                               include_simulated=settings.dry_run)
            except ValueError as e:
                log.info("Report noch nicht möglich: %s", e)
        runs = read_jsonl(data_path(settings, "runs.jsonl"), limit=29)
        for warning in resource_warnings(settings.data_dir):
            notifier.send("QuantDesk: Server-Ressourcen", warning, "error")
            run_row["errors"].append(warning)
        events_path = data_path(settings, "events.jsonl")
        append_events(events_path, events)
        status = build_status(engine, settings, fills, net_liq, report, runs)
        status["events"] = read_jsonl(events_path, limit=60)
        spy = services.marketdata.get_quote("SPY")
        acc = status["c_account"]
        write_snapshot(data_path(settings, "snapshots.jsonl"), {
            "day": str(day), "ts": time.time(), "c_value": acc["value"], "c_invested": acc["invested"],
            "c_cash": acc["cash"], "budget": acc["budget"], "net_liquidation": net_liq,
            "spy": spy.price if spy else None,
        })
        run_row.update(
            ok=bool(authed),
            errors=(run_row["errors"] + [e.message for e in events if e.level == "error"])[:20],
            fills=sum(1 for e in events if e.level == "order" and ("gefüllt" in e.message or "nachgebucht" in e.message)),
            orders=sum(1 for e in events if e.level == "order" and "platziert" in e.message),
        )
        status["runs"] = runs + [run_row | {"duration": round(time.time() - started, 1)}]
        save_json(data_path(settings, "status.json"), status)
        title, text = _summary(mode, day, events, status)
        if mode == "trade" or run_row["fills"] or run_row["errors"]:
            notifier.send(title, text, "error" if run_row["errors"] else "info")
        for note in run_row.get("notes", []):
            text += "\n" + note
        if notifier.failures:
            log.warning("Push: %s Nachricht(en) nicht zugestellt (%s)", notifier.failures, notifier.last_error)
        print(text)
        if mode == "trade":
            ping("success" if authed else "fail", text)
        return 0 if authed else 1
    except Exception as e:  # nie still sterben: Fehler melden, sauber beenden
        run_row["errors"].append(f"{type(e).__name__}: {e}")
        log.error("Lauf fehlgeschlagen:\n%s", traceback.format_exc())
        notifier.send("QuantDesk: Lauf fehlgeschlagen", f"{type(e).__name__}: {e}", "error")
        if mode == "trade":
            ping("fail", f"{type(e).__name__}: {e}")
        return 1
    finally:
        run_row["duration"] = round(time.time() - started, 1)
        try:
            append_jsonl(data_path(settings, "runs.jsonl"), run_row)
        except OSError:
            pass
        if services is not None:
            try:
                services.broker.close()
            except Exception:
                pass
        if os.path.exists(lock):
            os.remove(lock)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("trade", "reconcile"))
    ap.add_argument("--force", action="store_true", help="auch an Nicht-Handelstagen laufen")
    ap.add_argument("--no-push", action="store_true", help="keine Push-Nachrichten senden")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(os.getenv("QUANTDESK_LOG_FILE", "quantdesk.log"),
                                                               encoding="utf-8")],
    )
    return run(args.mode, force=args.force, push=not args.no_push)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    sys.exit(main())
