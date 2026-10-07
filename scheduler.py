"""Zeitplan für den Server-Container (alle Zeiten New York):

    NYSE-Handelstag   10:00  python run_daily.py trade       Entscheid mit Vortageskurs, Handel kurz nach Eröffnung
                      11:00  Wächter                        Push, falls bis jetzt kein erfolgreicher Handelslauf
                      15:30  python run_daily.py reconcile   nur Fills abgleichen, danach Backup (restic -> B2)
    Feiertag (Mo–Fr)  10:00  Healthchecks-Ping "kein Handelstag", damit die Überwachung von aussen nicht alarmiert
    Wochenende               nichts

Jeder Lauf ist ein eigener Prozess (ein Absturz trifft nur diesen Lauf).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import signal
import subprocess
import sys
import time

from quantdesk.healthcheck import ping
from quantdesk.schedule import NEW_YORK, is_trading_day
from quantdesk.status import read_jsonl

log = logging.getLogger("scheduler")

SCHEDULE = ((dt.time(10, 0), "trade"), (dt.time(11, 0), "watchdog"), (dt.time(15, 30), "reconcile"))
HOLIDAY = ((dt.time(10, 0), "holiday"),)
RUN_TIMEOUT_SECONDS = 30 * 60
_stop = False


def next_run(now: dt.datetime, trading_day=is_trading_day) -> tuple[dt.datetime, str]:
    """Nächster geplanter Schritt nach `now` (als Zeit in New York) und sein Modus."""
    local = now.astimezone(NEW_YORK)
    for offset in range(0, 15):
        day = local.date() + dt.timedelta(days=offset)
        plan = SCHEDULE if trading_day(day) else HOLIDAY if day.weekday() < 5 else ()
        for at, mode in plan:
            when = dt.datetime.combine(day, at, tzinfo=NEW_YORK)
            if when > local:
                return when, mode
    raise RuntimeError("Kein Termin in den nächsten 14 Tagen gefunden – Kalender prüfen.")


def trade_ok_today(runs: list[dict], day: dt.date) -> bool:
    return any(r.get("mode") == "trade" and r.get("day") == str(day) and r.get("ok") for r in runs)


def watchdog(data_dir: str, day: dt.date, notifier) -> bool:
    """True = alles ok. Sonst Push 'kein Lauf bis 11:00 New York'."""
    runs = read_jsonl(os.path.join(data_dir, "runs.jsonl"), limit=50)
    if trade_ok_today(runs, day):
        return True
    notifier.send("QuantDesk: heute kein Lauf",
                  f"Bis 11:00 New York gab es am {day:%d.%m.} keinen erfolgreichen Handelslauf. "
                  "Gateway eingeloggt? `docker compose logs bot ib-gateway`", "error")
    return False


def execute(mode: str, day: dt.date, runner=subprocess.run, notifier=None, data_dir: str | None = None) -> int:
    if mode in ("trade", "reconcile"):
        try:
            code = runner([sys.executable, "run_daily.py", mode], timeout=RUN_TIMEOUT_SECONDS).returncode
        except subprocess.TimeoutExpired:
            log.error("Lauf %s nach %s min abgebrochen", mode, RUN_TIMEOUT_SECONDS // 60)
            code = 1
        log.info("Lauf %s beendet mit Code %s", mode, code)
        if mode == "reconcile":  # Backup nach dem letzten Lauf des Tages
            try:
                runner([sys.executable, "-m", "quantdesk.backup", "run"], timeout=RUN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                log.error("Backup abgebrochen (Zeitüberschreitung)")
        return code
    if mode == "watchdog":
        return 0 if watchdog(data_dir or os.getenv("QUANTDESK_DATA_DIR", "data"), day, notifier) else 1
    if mode == "holiday":
        ping("success", f"{day}: kein NYSE-Handelstag")
        return 0
    raise ValueError(mode)


def _handle_signal(signum, frame) -> None:
    global _stop
    _stop = True
    log.info("Signal %s – Scheduler beendet sich nach dem aktuellen Schritt.", signum)


def main() -> int:
    from quantdesk.config import load_settings
    from quantdesk.notify import Notifier

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    settings = load_settings()
    notifier = Notifier.from_settings(settings)
    while not _stop:
        when, mode = next_run(dt.datetime.now(dt.timezone.utc))
        log.info("Nächster Schritt: %s (%s, New York)", mode, when.strftime("%a %d.%m.%Y %H:%M"))
        while not _stop:
            remaining = (when - dt.datetime.now(dt.timezone.utc)).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 60))
        if _stop:
            break
        try:
            execute(mode, when.date(), notifier=notifier, data_dir=settings.data_dir)
        except Exception:  # der Scheduler selbst darf nie sterben
            log.exception("Schritt %s fehlgeschlagen", mode)
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
