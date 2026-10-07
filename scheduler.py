"""Zeitplan für den Server-Container: startet run_daily.py an jedem NYSE-Handelstag

    10:00 New York  ->  python run_daily.py trade       (Entscheid mit Vortageskurs, Handel kurz nach Eröffnung)
    15:30 New York  ->  python run_daily.py reconcile   (nur Fills abgleichen, keine neuen Entscheide)

Jeder Lauf ist ein eigener Prozess (ein Absturz trifft nur diesen Lauf). Feiertage und Wochenenden werden übersprungen.
"""
from __future__ import annotations

import datetime as dt
import logging
import signal
import subprocess
import sys
import time

from quantdesk.schedule import NEW_YORK, is_trading_day

log = logging.getLogger("scheduler")

SCHEDULE = ((dt.time(10, 0), "trade"), (dt.time(15, 30), "reconcile"))
RUN_TIMEOUT_SECONDS = 30 * 60
_stop = False


def next_run(now: dt.datetime, trading_day=is_trading_day) -> tuple[dt.datetime, str]:
    """Nächster geplanter Lauf nach `now` (als Zeit in New York) und sein Modus."""
    local = now.astimezone(NEW_YORK)
    for offset in range(0, 15):
        day = local.date() + dt.timedelta(days=offset)
        if not trading_day(day):
            continue
        for at, mode in SCHEDULE:
            when = dt.datetime.combine(day, at, tzinfo=NEW_YORK)
            if when > local:
                return when, mode
    raise RuntimeError("Kein Handelstag in den nächsten 14 Tagen gefunden – Kalender prüfen.")


def _handle_signal(signum, frame) -> None:
    global _stop
    _stop = True
    log.info("Signal %s – Scheduler beendet sich nach dem aktuellen Schritt.", signum)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    while not _stop:
        when, mode = next_run(dt.datetime.now(dt.timezone.utc))
        log.info("Nächster Lauf: %s (%s, New York)", mode, when.strftime("%a %d.%m.%Y %H:%M"))
        while not _stop:
            remaining = (when - dt.datetime.now(dt.timezone.utc)).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 60))
        if _stop:
            break
        try:
            result = subprocess.run([sys.executable, "run_daily.py", mode], timeout=RUN_TIMEOUT_SECONDS)
            log.info("Lauf %s beendet mit Code %s", mode, result.returncode)
        except subprocess.TimeoutExpired:
            log.error("Lauf %s nach %s min abgebrochen", mode, RUN_TIMEOUT_SECONDS // 60)
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
