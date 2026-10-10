"""Lotse – ein Lauf, in der Reihenfolge der Regeln. Verdrahtet Konto, Tagebuch, Push und Tims Logik.

    python -m lotse.lauf                     # mit lotse/config.toml
    python -m lotse.lauf --config pfad.toml

Ablauf:
  Regel 1   Zieldatei lesen, Inhalt ins Tagebuch
  Regel 19  Orders früherer Läufe mit dem Broker abgleichen (nichts wird doppelt gesendet)
  Regel 3   Cash, Positionen, offene Orders lesen
  Regel 4   eigene offene Orders früherer Läufe stornieren, danach neu lesen
  Regel 2   Kurse holen
  Regel 0–18  logik.plane (Tim)
  Regel 20  Stück und Limit (Tim), Limit-Order nur bis Handelsschluss
  Regel 21  VORSCHLAG: nichts an den Broker, nur Tagebuch + Push
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import tomllib
from dataclasses import dataclass

from lotse import logik
from lotse.konto import Konto, KontoFehler
from lotse.push import Push
from lotse.tagebuch import AUSGEFUEHRT, GESENDET, STORNIERT, VERWORFEN, VORSCHLAG, Tagebuch

log = logging.getLogger("lotse")
CONFIG = os.path.join(os.path.dirname(__file__), "config.toml")
MODI = ("VORSCHLAG", "PAPER", "ECHT")
OFFEN = "TODO"
BOERSE_KALENDER = "XSWX"  # SIX Swiss Exchange
# Bis Tim entschieden hat, wie Lotse sein Geld vom Geld von Bot C trennt (beide auf DUO844164), sendet Lotse
# keine Orders. Siehe PR "Funde", Punkt 1. Tim setzt das bewusst auf True, wenn die Frage geklärt ist.
PAPER_FREIGEGEBEN = False
PAPER_GESPERRT = "PAPER ist gesperrt: Lotse teilt das Paper-Konto mit Bot C (Cash und Positionen). Erst klären, " \
                 "welches Geld Lotse gehört (PR, Funde Punkt 1), dann PAPER_FREIGEGEBEN in lauf.py auf True setzen."


@dataclass
class Ergebnis:
    ok: bool
    text: str
    orders: int = 0


# --------------------------------------------------------------------------- Config (Regel 1)
def lies_config(pfad: str = CONFIG) -> tuple[dict, str]:
    with open(pfad, "rb") as f:
        roh = f.read()
    return tomllib.loads(roh.decode("utf-8")), roh.decode("utf-8")


def pruefe_config(cfg: dict) -> list[str]:
    """Ist die Zieldatei vollständig und in sich stimmig? Liefert die Probleme als Text (leer = in Ordnung)."""
    probleme = []
    if cfg.get("modus") not in MODI:
        probleme.append(f"modus muss einer von {MODI} sein, ist {cfg.get('modus')!r}")
    fehlend = [teil for teil in ("wertpapiere", "ziel", "grenzen", "abbau", "gebuehren", "ausfuehren",
                                 "verbindung", "push", "ablage") if teil not in cfg]
    if fehlend:
        return probleme + [f"Abschnitt [{teil}] fehlt" for teil in fehlend]
    if list(cfg["wertpapiere"]) != list(cfg["ziel"]):
        probleme.append("[wertpapiere] und [ziel] müssen dieselben Namen in derselben Reihenfolge haben")
    offen = [name for name, papier in cfg["wertpapiere"].items() if papier == OFFEN]
    if offen:
        probleme.append(f"Wertpapiere noch nicht gewählt: {', '.join(offen)} (siehe lotse/WERTPAPIERE.md)")
    zahlen = list(cfg["ziel"].values()) + list(cfg["grenzen"].values()) + [cfg["gebuehren"].get("pro_order")] \
        + list(cfg["ausfuehren"].values())
    if not all(isinstance(z, (int, float)) and not isinstance(z, bool) for z in zahlen):
        probleme.append("In [ziel], [grenzen], [gebuehren] und [ausfuehren] stehen nur Zahlen")
    if not all(isinstance(p, str) for p in cfg["abbau"].get("liste", [])):
        probleme.append("[abbau] liste enthält nur Börsenkürzel als Text")
    return probleme


def einstellungen(cfg: dict) -> logik.Einstellungen:
    """config.toml in die Form bringen, die logik.py erwartet: Ziel je Börsenkürzel, in Config-Reihenfolge."""
    g, geb, aus = cfg["grenzen"], cfg["gebuehren"], cfg["ausfuehren"]
    ziel = {cfg["wertpapiere"][name]: float(prozent) for name, prozent in cfg["ziel"].items()}
    grenzen = logik.Grenzen(
        mindestbetrag=float(g["mindestbetrag"]), schwelle=float(g["schwelle"]), mindestdepot=float(g["mindestdepot"]),
        puffer_prozent=float(g["puffer_prozent"]), max_pro_order=float(g["max_pro_order"]),
        max_pro_tag=float(g["max_pro_tag"]), max_orders_pro_tag=int(g["max_orders_pro_tag"]),
        gebuehr_pro_order=float(geb["pro_order"]), limit_abstand_prozent=float(aus["limit_abstand_prozent"]),
        bruchstueck_stellen=int(aus["bruchstueck_stellen"]))
    return logik.Einstellungen(ziel, tuple(cfg["abbau"].get("liste", [])), grenzen)


# --------------------------------------------------------------------------- Börsenkalender
def letzter_handelstag(heute: dt.date) -> dt.date:
    """Letzter abgeschlossener Handelstag der SIX vor `heute` (Feiertage berücksichtigt)."""
    import exchange_calendars as xc
    import pandas as pd

    kalender = xc.get_calendar(BOERSE_KALENDER)
    return kalender.date_to_session(pd.Timestamp(heute) - pd.Timedelta(days=1), direction="previous").date()


# --------------------------------------------------------------------------- Regel 19: Abgleich nach Absturz
def gleiche_ab(konto: Konto, tagebuch: Tagebuch, jetzt: dt.datetime) -> list[str]:
    """Orders früherer Läufe mit dem Broker abgleichen. Nichts wird erneut gesendet. Liefert Hinweise."""
    hinweise = []
    ausfuehrungen = konto.ausfuehrungen()
    for a in ausfuehrungen:  # Regel 15: nur die gemeldete Menge zählt
        if tagebuch.buche_ausfuehrung(a.ref, a.exec_id, a.papier, a.seite, a.menge, a.preis, jetzt):
            hinweise.append(f"{a.papier}: {a.seite} {a.menge:g} Stück zu {a.preis} gebucht ({a.ref})")
    beim_broker = {o.ref for o in konto.offene_orders()} | {a.ref for a in ausfuehrungen}
    for ref in tagebuch.unbestaetigt():  # notiert, aber Senden nie bestätigt (Absturz)
        if ref in beim_broker:
            tagebuch.abhaken(ref, GESENDET, jetzt, hinweis="beim Abgleich beim Broker gefunden")
        else:
            tagebuch.abhaken(ref, VERWORFEN, jetzt, hinweis="nie beim Broker angekommen – wird nicht erneut gesendet")
            hinweise.append(f"{ref}: notiert, aber nie gesendet – verworfen (nicht wiederholt)")
    gefuellt = {a.ref for a in ausfuehrungen}
    offen = {o.ref for o in konto.offene_orders()}
    for ref in tagebuch.gesendet_offen():
        if ref in gefuellt and ref not in offen:
            tagebuch.abhaken(ref, AUSGEFUEHRT, jetzt)
    return hinweise


# --------------------------------------------------------------------------- der Lauf
def laufe(cfg: dict, zieldatei: str, konto: Konto, tagebuch: Tagebuch, push: Push, jetzt: dt.datetime,
          handelstag=letzter_handelstag) -> Ergebnis:
    probleme = pruefe_config(cfg)
    if probleme:
        return _stopp(push, "Zieldatei unvollständig: " + "; ".join(probleme))
    modus = cfg["modus"]
    if modus == "ECHT":
        return _stopp(push, "Modus ECHT ist in dieser Stufe nicht erlaubt (nur Paper).")
    if modus == "PAPER" and not PAPER_FREIGEGEBEN:
        return _stopp(push, PAPER_GESPERRT)
    e = einstellungen(cfg)
    nummer = tagebuch.neuer_lauf(zieldatei, jetzt, modus)  # Regel 1
    try:
        konto.verbinden()
        hinweise = gleiche_ab(konto, tagebuch, jetzt) if modus != "VORSCHLAG" else []
        offene = konto.offene_orders()  # Regel 3
        if offene and modus != "VORSCHLAG":  # Regel 4 (im VORSCHLAG wird nichts an den Broker geschickt)
            bekannt = tagebuch.zustaende()
            for o in offene:
                konto.storniere(o)
                if o.ref in bekannt:
                    tagebuch.abhaken(o.ref, STORNIERT, jetzt)
            offene = konto.offene_orders()
        papiere = list(e.ziel) + [p for p in e.abbau if p not in e.ziel]
        heute_betrag, heute_orders = tagebuch.heute(jetzt.date())
        lage = logik.Lage(cash=konto.cash(), stueck=konto.positionen(), eigene_stueck=tagebuch.eigene_stueck(),
                          kurse=konto.kurse(papiere), letzter_handelstag=handelstag(jetzt.date()),
                          gesamt_letzter_lauf=tagebuch.gesamt_letzter_lauf(), heute_betrag=heute_betrag,
                          heute_orders=heute_orders)
    except KontoFehler as fehler:
        return _ende(tagebuch, nummer, jetzt, _stopp(push, f"Broker: {fehler}"))
    if offene:
        hinweise.append(f"{len(offene)} offene Lotse-Order(s) beim Broker (im VORSCHLAG nicht storniert)")

    try:
        plan = logik.plane(lage, e)  # Regel 0, 5–18 (Tim)
    except NotImplementedError as fehlt:
        return _ende(tagebuch, nummer, jetzt, _stopp(push, f"Logik noch nicht fertig: {fehlt}"))
    if plan.abbruch is not None:  # Regel 0
        return _ende(tagebuch, nummer, jetzt, _stopp(push, plan.abbruch.grund), _gesamt(lage))

    zeilen = []
    try:
        for i, order in enumerate(plan.orders, 1):
            kurs = lage.kurse[order.papier].wert
            stueck, limit = logik.regel_20_stueck_und_limit(order, kurs, e.grenzen.limit_abstand_prozent,
                                                            e.grenzen.bruchstueck_stellen)
            ref = tagebuch.notiere(nummer, i, order.papier, order.seite, order.betrag, stueck, limit, jetzt)
            if modus == "VORSCHLAG":  # Regel 21
                tagebuch.abhaken(ref, VORSCHLAG, jetzt)
            else:
                perm = konto.sende_limit(order.papier, order.seite, stueck, limit, ref)
                tagebuch.abhaken(ref, GESENDET, jetzt, perm_id=perm)
            zeilen.append(f"{order.seite} {order.papier} {stueck:g} Stück, Limit {limit:.2f} CHF ({order.betrag:.2f} CHF)")
    except NotImplementedError as fehlt:
        return _ende(tagebuch, nummer, jetzt, _stopp(push, f"Logik noch nicht fertig: {fehlt}"))
    except KontoFehler as fehler:
        return _ende(tagebuch, nummer, jetzt, _stopp(push, f"Broker: {fehler}"))

    titel = f"Lotse Lauf {nummer} – {'Vorschlag' if modus == 'VORSCHLAG' else modus}"
    text = "\n".join(zeilen) if zeilen else "Nichts zu tun."
    if hinweise:
        text += "\n" + "\n".join(hinweise)
    push.sende(titel, text)
    return _ende(tagebuch, nummer, jetzt, Ergebnis(True, text, len(zeilen)), _gesamt(lage))


def _gesamt(lage: logik.Lage) -> float | None:
    """Depotwert für Regel 12 im Tagebuch merken – mit Tims Regel 5, solange sie noch fehlt: nichts."""
    try:
        return logik.regel_5_gesamt(logik.regel_5_werte(lage.stueck, lage.kurse), lage.cash)
    except (NotImplementedError, TypeError):
        return None


def _stopp(push: Push, grund: str) -> Ergebnis:
    """Regel 0 im Gerüst: keine Order, Push mit Grund, Lauf beenden."""
    log.warning("Lotse stoppt: %s", grund)
    push.sende("Lotse – keine Order", grund, wichtig=True)
    return Ergebnis(False, grund)


def _ende(tagebuch: Tagebuch, nummer: int, jetzt: dt.datetime, ergebnis: Ergebnis,
          gesamt: float | None = None) -> Ergebnis:
    tagebuch.lauf_ende(nummer, ergebnis.text, jetzt, gesamt)
    return ergebnis


# --------------------------------------------------------------------------- Kommandozeile
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lotse: ein Lauf (Standard: Modus aus config.toml)")
    ap.add_argument("--config", default=CONFIG)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg, text = lies_config(args.config)
    v = cfg.get("verbindung", {})
    push = Push.aus_umgebung(cfg.get("push", {}).get("thema", "lotse"))
    try:
        konto = Konto(v["host"], int(v["port"]), int(v["client_id"]), v["konto"], v["boerse"], v["waehrung"])
    except (KeyError, KontoFehler) as fehler:
        print(f"Verbindung: {fehler}", file=sys.stderr)
        return 1
    ordner = os.getenv("LOTSE_ORDNER") or cfg.get("ablage", {}).get("ordner", "lotse-daten")
    try:
        ergebnis = laufe(cfg, text, konto, Tagebuch(ordner), push, dt.datetime.now())
    finally:
        konto.trennen()
    print(ergebnis.text)
    return 0 if ergebnis.ok else 1


if __name__ == "__main__":
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(errors="replace")
    sys.exit(main())
