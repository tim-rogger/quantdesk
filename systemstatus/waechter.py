"""Gateway-Wächter: meldet sich das IB Gateway nach einer IBKR-Wartung nicht von selbst wieder an,
startet der Wächter den Gateway-Container neu – und schickt einen Push.

    sudo python3 -m systemstatus.waechter              # Dauerbetrieb, prüft jede Minute (systemd-Dienst)
    sudo python3 -m systemstatus.waechter --einmal     # einmal prüfen und zeigen, was er tun würde (tut nichts)
    sudo python3 -m systemstatus.waechter --testlauf   # Ernstfall üben: EIN Neustart + Push, dann warten bis eingeloggt

Was er prüft: Antwortet die TWS-API, und ist ein Konto eingeloggt, das mit DU beginnt?
Was er tut, wenn nicht:
  - erst nach GRENZE_MINUTEN am Stück (kurze Aussetzer, z.B. der tägliche Neustart um 23:59, sind normal)
  - dann: Gateway-Container neu starten, Push "Gateway neu gestartet"
  - klappt es auch nach MAX_NEUSTARTS_IN_FOLGE Neustarts nicht: EIN Push "lässt sich nicht einloggen" und aufhören
    (nie eine Endlosschleife; höchstens MAX_NEUSTARTS_PRO_TAG Neustarts pro Tag)
  - ist es wieder eingeloggt: Push "wieder eingeloggt"
Was er nie tut: handeln, verkaufen, Orders anfassen, einen von Hand gestoppten Container starten.
Während der IBKR-Wartungsfenster schaut er nur zu (dann ist "ausgeloggt" normal).

Läuft als root auf dem Host (docker restart braucht den Docker-Socket) – NICHT im Dashboard-Container.
Nur Python-Standardbibliothek. Schreibt nur deploy/state/system/waechter.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

from systemstatus import tws

log = logging.getLogger("gatewaywaechter")

PRUEF_SEKUNDEN = 60
GRENZE_MINUTEN = 10               # so lange muss das Gateway am Stück ausgeloggt sein, bevor er eingreift
WARTEN_NACH_NEUSTART_MINUTEN = 8  # so lange bekommt das Gateway nach einem Neustart Zeit für die Anmeldung
MAX_NEUSTARTS_IN_FOLGE = 2
MAX_NEUSTARTS_PRO_TAG = 4
CLIENT_ID = 92                    # eigene Client-ID (Bot C 17, Lotse 27, Sammler 91) – sonst stören sie sich
TWS_PORT = 4004
NTFY_LOKAL = "http://127.0.0.1:8090"
NEW_YORK = ZoneInfo("America/New_York")


# ====================================================================== Entscheiden (reine Logik, ohne Ein-/Ausgabe)
def neuer_zustand() -> dict:
    return {"kaputt_seit": None, "neustarts_folge": 0, "letzter_neustart": None, "tag": "", "neustarts_heute": 0,
            "aufgegeben": False}


def in_wartung(jetzt: float) -> bool:
    """IBKR-Wartung (New-York-Zeit): täglich rund um Mitternacht, dazu Samstag bis Sonntagmorgen.
    In dieser Zeit ist "ausgeloggt" normal – der Wächter schaut nur zu."""
    ny = dt.datetime.fromtimestamp(jetzt, NEW_YORK)
    minuten = ny.hour * 60 + ny.minute
    if ny.weekday() == 5:                         # Samstag
        return True
    if ny.weekday() == 6 and ny.hour < 8:         # Sonntag bis 08:00 (Kaltstart um 07:05, siehe docker-compose.yml)
        return True
    return minuten >= 23 * 60 + 30 or minuten < 75  # täglich 23:30–01:15


def entscheide(zustand: dict, gesund: bool, jetzt: float) -> tuple[dict, list[tuple]]:
    """Was ist jetzt zu tun? Liefert (neuer Zustand, Aktionen). Aktionen:
        ("neustart",)                    Gateway-Container neu starten
        ("push", titel, text, wichtig)   Nachricht aufs Handy
    """
    z = dict(zustand)
    aktionen: list[tuple] = []
    tag = dt.datetime.fromtimestamp(jetzt, NEW_YORK).strftime("%Y-%m-%d")
    if z["tag"] != tag:  # neuer Tag: Zähler zurück, nach dem Aufgeben wird es neu versucht
        z.update(tag=tag, neustarts_heute=0, neustarts_folge=0, aufgegeben=False)

    if gesund:
        if z["kaputt_seit"] is not None and (jetzt - z["kaputt_seit"] >= GRENZE_MINUTEN * 60 or z["neustarts_folge"]):
            dauer = round((jetzt - z["kaputt_seit"]) / 60)
            wie = " nach automatischem Neustart" if z["neustarts_folge"] else " von selbst"
            aktionen.append(("push", "QuantDesk: Gateway wieder eingeloggt",
                             f"Das Gateway war {dauer} min ausgeloggt und ist{wie} wieder eingeloggt.", False))
        z.update(kaputt_seit=None, neustarts_folge=0, aufgegeben=False)
        return z, aktionen

    if z["kaputt_seit"] is None:
        z["kaputt_seit"] = jetzt
    if in_wartung(jetzt) or z["aufgegeben"]:
        return z, aktionen
    if jetzt - z["kaputt_seit"] < GRENZE_MINUTEN * 60:
        return z, aktionen
    if z["letzter_neustart"] and jetzt - z["letzter_neustart"] < WARTEN_NACH_NEUSTART_MINUTEN * 60:
        return z, aktionen  # dem letzten Neustart noch Zeit lassen

    dauer = round((jetzt - z["kaputt_seit"]) / 60)
    if z["neustarts_folge"] >= MAX_NEUSTARTS_IN_FOLGE or z["neustarts_heute"] >= MAX_NEUSTARTS_PRO_TAG:
        z["aufgegeben"] = True
        aktionen.append(("push", "QuantDesk: Gateway lässt sich nicht einloggen – bitte prüfen",
                         f"Seit {dauer} min ausgeloggt, {z['neustarts_folge']} Neustart(s) haben nicht geholfen. "
                         "Der Wächter hört für heute auf. Prüfen: docker compose logs --tail 100 ib-gateway", True))
        return z, aktionen

    z["neustarts_folge"] += 1
    z["neustarts_heute"] += 1
    z["letzter_neustart"] = jetzt
    aktionen.append(("neustart",))
    aktionen.append(("push", "QuantDesk: Gateway neu gestartet",
                     f"War seit {dauer} min nicht eingeloggt. Automatischer Neustart (Versuch {z['neustarts_folge']}).",
                     False))
    return z, aktionen


def ist_gesund(ergebnis: dict) -> bool:
    """TWS-API antwortet UND mindestens ein Konto eingeloggt, alle beginnen mit DU (nur Paper)."""
    konten = ergebnis.get("konten") or []
    return bool(konten) and all(k.upper().startswith("DU") for k in konten)


# ====================================================================== Ein-/Ausgabe
def befehl(argumente: list[str], timeout: float) -> str | None:
    try:
        p = subprocess.run(argumente, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def gateway_container() -> dict | None:
    """Name, Zustand und interne Adresse des Gateway-Containers (oder None, wenn Docker nicht antwortet)."""
    name = befehl(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=quantdesk",
                   "--filter", "label=com.docker.compose.service=ib-gateway", "--format", "{{.Names}}"], 15)
    if not name or not name.split():
        return None
    name = name.split()[0]
    text = befehl(["docker", "inspect", name], 15)
    try:
        info = json.loads(text or "[]")[0]
    except (ValueError, IndexError):
        return None
    netze = (info.get("NetworkSettings") or {}).get("Networks") or {}
    return {"name": name, "zustand": (info.get("State") or {}).get("Status"),
            "ip": next((n.get("IPAddress") for n in netze.values() if n.get("IPAddress")), None)}


def neu_starten(name: str) -> bool:
    log.warning("Starte Gateway-Container %s neu", name)
    return befehl(["docker", "restart", name], 180) is not None


def env_werte(pfad: str, schluessel: tuple[str, ...]) -> dict:
    """Nur diese Werte aus der .env lesen. Der Token wird nie geloggt oder gespeichert."""
    out = {}
    try:
        with open(pfad, encoding="utf-8") as f:
            for zeile in f:
                name, _, wert = zeile.strip().partition("=")
                if name.strip() in schluessel:
                    out[name.strip()] = wert.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def push(env_pfad: str, titel: str, text: str, wichtig: bool) -> None:
    """Nachricht über ntfy auf dem Server (lokal, nicht übers Internet). Fehler werden nur geloggt."""
    werte = env_werte(env_pfad, ("NTFY_TOPIC", "NTFY_TOKEN"))
    nachricht = {"topic": werte.get("NTFY_TOPIC") or "quantdesk", "title": titel, "message": text,
                 "priority": 5 if wichtig else 3, "tags": ["rotating_light" if wichtig else "electric_plug"]}
    kopf = {"Content-Type": "application/json"}
    if werte.get("NTFY_TOKEN"):
        kopf["Authorization"] = "Bearer " + werte["NTFY_TOKEN"]
    anfrage = urllib.request.Request(NTFY_LOKAL, data=json.dumps(nachricht).encode("utf-8"), headers=kopf)
    try:
        with urllib.request.urlopen(anfrage, timeout=10):
            pass
        log.info("Push gesendet: %s", titel)
    except (OSError, ValueError) as fehler:
        log.warning("Push nicht gesendet (%s): %s", type(fehler).__name__, titel)


def lies_zustand(pfad: str) -> dict:
    try:
        with open(pfad, encoding="utf-8") as f:
            return neuer_zustand() | json.load(f)
    except (OSError, ValueError):
        return neuer_zustand()


def schreibe_zustand(pfad: str, zustand: dict, letzte_pruefung: dict) -> None:
    """waechter.json – auch der Sammler liest sie, damit die App die Selbstheilung zeigen kann."""
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    hilfsdatei = pfad + ".tmp"
    with open(hilfsdatei, "w", encoding="utf-8") as f:
        json.dump(zustand | {"letzte_pruefung": letzte_pruefung}, f, indent=1)
    os.chmod(hilfsdatei, 0o644)
    os.replace(hilfsdatei, pfad)


# ====================================================================== ein Durchgang
def pruefen() -> tuple[dict | None, dict]:
    """(Container, Prüfergebnis). Container None = Docker antwortet nicht."""
    c = gateway_container()
    if c is None or c["zustand"] != "running" or not c["ip"]:
        return c, {"konten": None, "fehler": "Container nicht lesbar oder nicht gestartet"}
    return c, tws.konten_abfragen(c["ip"], TWS_PORT, client_id=CLIENT_ID, timeout=8)


def durchgang(deploy: str, nur_zeigen: bool = False) -> list[tuple]:
    pfad = os.path.join(deploy, "state", "system", "waechter.json")
    env_pfad = os.path.join(deploy, ".env")
    jetzt = time.time()
    zustand = lies_zustand(pfad)
    c, ergebnis = pruefen()
    if c is None or c["zustand"] != "running":
        # Docker antwortet nicht, oder Tim hat den Container von Hand gestoppt: nicht eingreifen.
        log.info("Gateway-Container %s – Wächter greift nicht ein", "nicht lesbar" if c is None else c["zustand"])
        aktionen = []
        zustand["kaputt_seit"] = None
    else:
        zustand, aktionen = entscheide(zustand, ist_gesund(ergebnis), jetzt)
    letzte = {"zeit": jetzt, "gesund": ist_gesund(ergebnis), "fehler": ergebnis.get("fehler", "")}
    if nur_zeigen:
        return aktionen
    for aktion in aktionen:
        if aktion[0] == "neustart" and c is not None:
            neu_starten(c["name"])
        elif aktion[0] == "push":
            push(env_pfad, aktion[1], aktion[2], aktion[3])
    schreibe_zustand(pfad, zustand, letzte)
    return aktionen


def testlauf(deploy: str) -> int:
    """Den Ernstfall üben: genau EIN Neustart mit Push, dann warten (höchstens 10 min), bis das Gateway wieder
    eingeloggt ist, und das ebenfalls melden. Neustarten ist harmlos – gehandelt wird dabei nichts."""
    env_pfad = os.path.join(deploy, ".env")
    c = gateway_container()
    if c is None or c["zustand"] != "running":
        print("Gateway-Container läuft nicht – Testlauf abgebrochen.")
        return 1
    start = time.time()
    push(env_pfad, "QuantDesk: Gateway neu gestartet (Testlauf)", "Wächter-Testlauf: Neustart von Hand ausgelöst.", False)
    if not neu_starten(c["name"]):
        print("docker restart fehlgeschlagen.")
        return 1
    while time.time() - start < 10 * 60:
        time.sleep(20)
        _, ergebnis = pruefen()
        if ist_gesund(ergebnis):
            dauer = round((time.time() - start) / 60, 1)
            push(env_pfad, "QuantDesk: Gateway wieder eingeloggt (Testlauf)", f"Nach {dauer} min wieder eingeloggt.", False)
            print(f"OK: nach {dauer} min wieder eingeloggt.")
            return 0
        print("… noch nicht eingeloggt:", ergebnis.get("fehler", ""))
    push(env_pfad, "QuantDesk: Gateway lässt sich nicht einloggen (Testlauf)",
         "Nach 10 min nicht eingeloggt. docker compose logs --tail 100 ib-gateway", True)
    print("NICHT eingeloggt nach 10 min – docker compose logs --tail 100 ib-gateway")
    return 1


def main(argv: list[str] | None = None) -> int:
    standard = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deploy", default=standard, help="Ordner deploy/ (mit state/ und .env)")
    ap.add_argument("--einmal", action="store_true", help="einmal prüfen und zeigen, was er täte – ohne es zu tun")
    ap.add_argument("--testlauf", action="store_true", help="einen Neustart üben (mit Push)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.testlauf:
        return testlauf(args.deploy)
    if args.einmal:
        c, ergebnis = pruefen()
        print("Container:", c["zustand"] if c else "nicht lesbar")
        print("Eingeloggt (Paper):", "ja" if ist_gesund(ergebnis) else f"NEIN – {ergebnis.get('fehler', '')}")
        print("In IBKR-Wartungsfenster:", "ja" if in_wartung(time.time()) else "nein")
        print("Würde jetzt tun:", durchgang(args.deploy, nur_zeigen=True) or "nichts")
        return 0
    log.info("Gateway-Wächter läuft, prüft alle %s s.", PRUEF_SEKUNDEN)
    while True:
        try:
            durchgang(args.deploy)
        except Exception:  # der Wächter darf nie sterben
            log.exception("Durchgang fehlgeschlagen")
        time.sleep(PRUEF_SEKUNDEN)


if __name__ == "__main__":
    sys.exit(main())
