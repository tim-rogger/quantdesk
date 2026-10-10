"""Lotse – Zeitplan: ein Lauf an jedem SIX-Handelstag zur Uhrzeit aus config.toml ([zeitplan]).

    python -m lotse.zeitplan

An Feiertagen und Wochenenden läuft nichts (Börsenkalender XSWX). Startet der Container erst nach der Uhrzeit
(z.B. nach einem Neustart) und gab es heute noch keinen Lauf, wird er bis `nachholen_bis` nachgeholt.
Jeder Lauf ist ein eigener Prozess: ein Absturz trifft nur diesen Lauf, der Zeitplan läuft weiter.
Gehandelt wird trotzdem selten – an den meisten Tagen gibt es nichts zu tun (Regel 9).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import signal
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from lotse.lauf import BOERSE_KALENDER, CONFIG, lies_config
from lotse.push import Push
from lotse.tagebuch import Tagebuch

log = logging.getLogger("lotse.zeitplan")
_stop = False


def ist_handelstag(tag: dt.date) -> bool:
    """Ist die SIX an diesem Tag offen? (Feiertage laut Börsenkalender)"""
    import exchange_calendars as xc
    import pandas as pd

    return bool(xc.get_calendar(BOERSE_KALENDER).is_session(pd.Timestamp(tag)))


def _uhrzeit(text: str) -> dt.time:
    stunde, minute = text.split(":")
    return dt.time(int(stunde), int(minute))


def naechster_lauf(jetzt: dt.datetime, uhrzeit: dt.time, zone: ZoneInfo, handelstag=ist_handelstag) -> dt.datetime:
    """Nächster Lauf nach `jetzt`: der nächste Handelstag zur Uhrzeit (in der Zeitzone der Börse)."""
    lokal = jetzt.astimezone(zone)
    for tage in range(0, 15):
        tag = lokal.date() + dt.timedelta(days=tage)
        termin = dt.datetime.combine(tag, uhrzeit, tzinfo=zone)
        if termin > lokal and handelstag(tag):
            return termin
    raise RuntimeError("Kein Handelstag in den nächsten 14 Tagen – Börsenkalender prüfen.")


def jetzt_nachholen(jetzt: dt.datetime, uhrzeit: dt.time, bis: dt.time, zone: ZoneInfo, heute_gelaufen: bool,
                    handelstag=ist_handelstag) -> bool:
    """Heute ist Handelstag, die Uhrzeit ist vorbei, aber vor `bis`, und es gab heute noch keinen Lauf?"""
    lokal = jetzt.astimezone(zone)
    return (not heute_gelaufen and handelstag(lokal.date()) and uhrzeit <= lokal.time() < bis)


def starte_lauf(timeout_sekunden: int) -> int:
    """Einen Lauf als eigenen Prozess starten. Liefert den Exit-Code (Zeitüberschreitung = 124)."""
    try:
        return subprocess.run([sys.executable, "-m", "lotse.lauf"], timeout=timeout_sekunden).returncode
    except subprocess.TimeoutExpired:
        log.error("Lauf nach %s s abgebrochen (Zeitlimit)", timeout_sekunden)
        return 124


def _warte_bis(termin: dt.datetime, uhr, schlafen) -> bool:
    """Bis zum Termin schlafen (in Schritten, damit ein Stopp-Signal greift). False = gestoppt."""
    while not _stop:
        rest = (termin - uhr()).total_seconds()
        if rest <= 0:
            return True
        schlafen(min(rest, 60))
    return False


def main(argv: list[str] | None = None, uhr=None, schlafen=time.sleep, starte=starte_lauf,
         handelstag=ist_handelstag, runden: int | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg, _ = lies_config(CONFIG)
    plan = cfg["zeitplan"]
    zone = ZoneInfo(plan["zeitzone"])
    uhrzeit, bis = _uhrzeit(plan["uhrzeit"]), _uhrzeit(plan["nachholen_bis"])
    timeout = int(plan["lauf_timeout_minuten"]) * 60
    uhr = uhr or (lambda: dt.datetime.now(zone))
    ordner = os.getenv("LOTSE_ORDNER") or cfg.get("ablage", {}).get("ordner", "lotse-daten")
    push = Push.aus_umgebung(cfg.get("push", {}).get("thema", "lotse"))
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _beenden)

    def lauf_jetzt() -> None:
        code = starte(timeout)
        if code not in (0, 1):  # 1 = Lauf hat bewusst gestoppt (Regel 0) und selbst gepusht
            push.sende("Lotse – Lauf abgestürzt", f"Exit-Code {code}. `docker compose logs lotse`", wichtig=True)

    heute = uhr().astimezone(zone).date()
    if jetzt_nachholen(uhr(), uhrzeit, bis, zone, Tagebuch(ordner).lauf_am(heute), handelstag):
        log.info("Heutiger Lauf fehlt noch – wird nachgeholt")
        lauf_jetzt()
    erledigt = 0
    while not _stop and (runden is None or erledigt < runden):
        termin = naechster_lauf(uhr(), uhrzeit, zone, handelstag)
        log.info("Nächster Lauf: %s", termin.isoformat())
        if not _warte_bis(termin, uhr, schlafen):
            break
        lauf_jetzt()
        erledigt += 1
    return 0


def _beenden(*_args) -> None:
    global _stop
    _stop = True


if __name__ == "__main__":
    sys.exit(main())
