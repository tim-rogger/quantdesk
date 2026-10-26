"""Sammler starten:

    sudo python3 -m systemstatus                  # Dauerbetrieb, alle 30 s (so läuft er als systemd-Dienst)
    sudo python3 -m systemstatus --einmal         # einmal messen, system.json schreiben, Ergebnis zeigen

Muss im Repo-Ordner (~/quantdesk) gestartet werden. Braucht root, weil `ufw status` nur root lesen darf.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time

from systemstatus.bewerten import bewerten, notfall
from systemstatus.messen import Zwischenspeicher, sammeln

log = logging.getLogger("systemstatus")
TAKT_SEKUNDEN = 30


def schreibe_json(pfad: str, daten: dict) -> None:
    """Erst in eine Hilfsdatei schreiben, dann umbenennen: das Dashboard sieht nie eine halbe Datei."""
    ordner = os.path.dirname(pfad)
    os.makedirs(ordner, exist_ok=True)
    fd, hilfsdatei = tempfile.mkstemp(prefix=".tmp-", dir=ordner)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1, allow_nan=False)
    os.chmod(hilfsdatei, 0o644)  # das Dashboard läuft mit Tims UID und muss die Datei lesen dürfen
    os.replace(hilfsdatei, pfad)


def lies_merker(pfad: str) -> dict:
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def einmal(deploy: str, speicher: Zwischenspeicher, merker: dict) -> dict:
    """Ein Zyklus. Er schreibt system.json IMMER – auch wenn Sammeln oder Bewerten scheitert (dann die
    Notfall-Datei). Sonst würde die Seite genau dann "Daten veraltet" zeigen, wenn etwas kaputt ist."""
    ziel = os.path.join(deploy, "state", "system")
    try:
        ergebnis = bewerten(sammeln(deploy, speicher, merker))
    except Exception as fehler:
        log.exception("Messung fehlgeschlagen")
        ergebnis = notfall(time.time(), type(fehler).__name__)
    schreibe_json(os.path.join(ziel, "system.json"), ergebnis)
    try:
        schreibe_json(os.path.join(ziel, "merker.json"), dict(merker))
    except (OSError, ValueError, RuntimeError):
        log.exception("merker.json nicht geschrieben")  # nur Zusatzwissen – system.json ist schon geschrieben
    return ergebnis


def main(argv: list[str] | None = None) -> int:
    standard = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deploy", default=standard, help="Ordner deploy/ (mit state/, .env und bots.toml)")
    ap.add_argument("--einmal", action="store_true", help="nur einmal messen und das Ergebnis zeigen")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    speicher = Zwischenspeicher()
    merker = lies_merker(os.path.join(args.deploy, "state", "system", "merker.json"))
    if args.einmal:
        ergebnis = einmal(args.deploy, speicher, merker)
        for k in ergebnis["kaesten"]:
            print(f"{k['stufe']:6} {k['titel']:36} {k['kurz']}")
        for w in ergebnis["warnungen"]:
            print("WARNUNG:", w)
        return 0
    log.info("Sammler läuft, alle %s s.", TAKT_SEKUNDEN)
    while True:
        try:
            einmal(args.deploy, speicher, merker)
        except Exception:  # nur, wenn nicht einmal system.json geschrieben werden konnte (z.B. Platte voll)
            log.exception("system.json nicht geschrieben")
        time.sleep(TAKT_SEKUNDEN)


if __name__ == "__main__":
    sys.exit(main())
