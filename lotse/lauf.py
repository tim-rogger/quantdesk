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
import math
import os
import sys
import tomllib
from dataclasses import dataclass

from lotse import logik
from lotse.konto import Konto, KontoFehler
from lotse.kurse import YahooKurse
from lotse.push import Push
from lotse.tagebuch import AUSGEFUEHRT, GESENDET, STORNIERT, VERWORFEN, VORSCHLAG, Tagebuch

log = logging.getLogger("lotse")
CONFIG = os.path.join(os.path.dirname(__file__), "config.toml")
MODI = ("VORSCHLAG", "PAPER", "ECHT")
OFFEN = "TODO"
KURSQUELLEN = ("yahoo", "ibkr")
BOERSE_KALENDER = "XSWX"  # SIX Swiss Exchange


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
                                 "verbindung", "push", "ablage", "kurse") if teil not in cfg]
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
    quelle = cfg["kurse"].get("quelle")
    if quelle not in KURSQUELLEN:
        probleme.append(f"[kurse] quelle muss einer von {KURSQUELLEN} sein, ist {quelle!r}")
    elif quelle == "yahoo":
        symbole = cfg["kurse"].get("symbole", {})
        papiere = [p for p in cfg["wertpapiere"].values() if p != OFFEN] + list(cfg["abbau"].get("liste", []))
        fehlend = [p for p in papiere if not symbole.get(p)]
        if fehlend:
            probleme.append(f"[kurse.symbole] fehlt für: {', '.join(fehlend)}")
    return probleme


def einstellungen(cfg: dict) -> logik.Einstellungen:
    """config.toml in die Form bringen, die logik.py erwartet: Ziel je Börsenkürzel, in Config-Reihenfolge."""
    g, geb, aus = cfg["grenzen"], cfg["gebuehren"], cfg["ausfuehren"]
    ziel = {cfg["wertpapiere"][name]: float(prozent) for name, prozent in cfg["ziel"].items()}
    grenzen = logik.Grenzen(
        budget_chf=float(g["budget_chf"]), max_einzahlung_pro_lauf=float(g["max_einzahlung_pro_lauf"]),
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
        if tagebuch.buche_ausfuehrung(a.ref, a.exec_id, a.papier, a.seite, a.menge, a.preis, jetzt, a.gebuehr,
                                      a.zeit):
            hinweise.append(f"{a.papier}: {a.seite} {a.menge:g} Stück zu {a.preis} gebucht ({a.ref})")
        elif a.gebuehr is not None and a.exec_id in tagebuch.ohne_gebuehr():
            tagebuch.buche_gebuehr(a.exec_id, a.gebuehr, jetzt)  # Kommission kam erst nach der Ausführung
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


# --------------------------------------------------------------------------- Ausschüttungen
def erfasse_dividenden(cfg: dict, konto: Konto, tagebuch: Tagebuch, jetzt: dt.datetime) -> list[str]:
    """Ausschüttungen eigener Papiere aus der Flex-Abfrage buchen (senken "investiert"). Liefert Hinweise."""
    query_id = str(cfg.get("dividenden", {}).get("flex_query_id", "") or "")
    token = os.getenv("LOTSE_FLEX_TOKEN", "")
    if not query_id or not token:
        if tagebuch.eigene_stueck():
            return ["Ausschüttungen werden nicht erfasst (Flex-Abfrage nicht eingerichtet: [dividenden] "
                    "flex_query_id und LOTSE_FLEX_TOKEN)"]
        return []
    try:
        gutschriften = konto.dividenden(token, query_id)
        im_konto = konto.positionen()
    except KontoFehler as fehler:
        return [f"Ausschüttungen nicht gelesen: {fehler}"]
    hinweise = []
    for g in gutschriften:
        gebucht = tagebuch.buche_dividende(g.id, g.papier, g.betrag, g.datum, im_konto.get(g.papier), jetzt)
        if gebucht is not None:
            hinweise.append(f"{g.papier}: {g.art} {gebucht:+.2f} CHF gebucht ({g.datum:%d.%m.})")
    return hinweise


# --------------------------------------------------------------------------- Kurse (Regel 2)
def hole_kurse(cfg: dict, konto: Konto, papiere: list[str], kursquelle) -> tuple[dict, dict[str, str], list[str]]:
    """Kurse je Papier aus der Quelle laut config.toml. Liefert (Kurse, Quelle je Papier, Probleme).
    Ein Kurs in einer anderen Währung als das Konto ist ein Problem (Regel 0) – es wird nie umgerechnet."""
    if cfg["kurse"]["quelle"] == "ibkr":
        return konto.kurse(papiere), dict(konto.kursquellen), []
    waehrung = cfg["verbindung"]["waehrung"]
    kurse, quellen, probleme = {}, {}, []
    for papier in papiere:
        symbol = cfg["kurse"]["symbole"][papier]
        n = kursquelle.notierung(symbol)
        if n.wert is not None and n.waehrung != waehrung:
            probleme.append(f"Kurs von {papier} ({symbol}) ist in {n.waehrung or 'unbekannter Währung'} statt "
                            f"{waehrung} – wird nicht umgerechnet")
        kurse[papier] = logik.Kurs(n.wert, n.datum)
        if n.wert is not None:
            quellen[papier] = f"Yahoo {symbol}"
    return kurse, quellen, probleme


# --------------------------------------------------------------------------- der Lauf
def laufe(cfg: dict, zieldatei: str, konto: Konto, tagebuch: Tagebuch, push: Push, jetzt: dt.datetime,
          handelstag=letzter_handelstag, einzahlung_bestaetigt: bool = False, kursquelle=None) -> Ergebnis:
    probleme = pruefe_config(cfg)
    if probleme:
        return _stopp(push, "Zieldatei unvollständig: " + "; ".join(probleme))
    modus = cfg["modus"]
    if modus == "ECHT":
        return _stopp(push, "Modus ECHT ist in dieser Stufe nicht erlaubt (nur Paper).")
    e = einstellungen(cfg)
    nummer = tagebuch.neuer_lauf(zieldatei, jetzt, modus)  # Regel 1
    if einzahlung_bestaetigt:
        tagebuch.vermerke_bestaetigung(nummer, jetzt)
    try:
        konto.verbinden()
        hinweise = gleiche_ab(konto, tagebuch, jetzt) if modus != "VORSCHLAG" else []
        if modus != "VORSCHLAG":
            hinweise += erfasse_dividenden(cfg, konto, tagebuch, jetzt)
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
        kasse = tagebuch.kasse_letzter_lauf()
        kurse, quellen, kursprobleme = hole_kurse(cfg, konto, papiere, kursquelle or YahooKurse())
        lage = logik.Lage(cash=konto.cash(), stueck=konto.positionen(), eigene_stueck=tagebuch.eigene_stueck(),
                          kurse=kurse, letzter_handelstag=handelstag(jetzt.date()),
                          mindestdepot_erreicht=tagebuch.mindestdepot_erreicht(), heute_betrag=heute_betrag,
                          heute_orders=heute_orders, investiert=tagebuch.investiert(),
                          cash_letzter_lauf=kasse[0] if kasse else None,
                          investiert_letzter_lauf=kasse[1] if kasse else 0.0,
                          einzahlung_bestaetigt=einzahlung_bestaetigt)
    except KontoFehler as fehler:
        return _ende(tagebuch, nummer, jetzt, _stopp(push, f"Broker: {fehler}"))
    if offene:
        hinweise.append(f"{len(offene)} offene Lotse-Order(s) beim Broker (im VORSCHLAG nicht storniert)")
    if kursprobleme:  # Regel 0: falsche Währung
        return _ende(tagebuch, nummer, jetzt, _stopp(push, "; ".join(kursprobleme)))
    if quellen:
        hinweise.append("Kurse: " + ", ".join(f"{p} {q} ({_tag(lage.kurse[p].datum)})" for p, q in quellen.items()))

    try:
        plan = logik.plane(lage, e)  # Regel 0, 5–18 (Tim)
    except NotImplementedError as fehlt:
        return _ende(tagebuch, nummer, jetzt, _stopp(push, f"Logik noch nicht fertig: {fehlt}"))
    if plan.abbruch is not None:  # Regel 0
        return _ende(tagebuch, nummer, jetzt, _stopp(push, plan.abbruch.grund))

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
    tagebuch.vermerke_kasse(nummer, lage.cash, lage.investiert, jetzt)  # Ausgangspunkt für die nächste Einzahlung
    gesamt = _gesamt(lage, e)
    if gesamt is not None and gesamt >= e.grenzen.mindestdepot:  # Regel 12: einmal vermerkt, nie neu errechnet
        tagebuch.vermerke_mindestdepot(nummer, gesamt, jetzt)
    push.sende(titel, text)
    return _ende(tagebuch, nummer, jetzt, Ergebnis(True, text, len(zeilen)), gesamt)


def _gesamt(lage: logik.Lage, e: logik.Einstellungen) -> float | None:
    """Lotse-Depotwert (Regel 5) fürs Tagebuch. None, wenn er sich nicht rechnen lässt."""
    try:
        gesamt = logik.regel_5_gesamt(logik.regel_5_werte(lage.eigene_stueck, lage.kurse), lage.cash,
                                      e.grenzen.budget_chf, lage.investiert)
    except (NotImplementedError, TypeError):
        return None
    return gesamt if math.isfinite(gesamt) else None


def _tag(datum: dt.date | None) -> str:
    return f"{datum:%d.%m.}" if datum else "ohne Datum"


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
    ap.add_argument("--einzahlung-bestaetigt", action="store_true",
                    help="eine ungewöhnlich grosse Einzahlung für diesen Lauf bestätigen (Regel 0)")
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
        ergebnis = laufe(cfg, text, konto, Tagebuch(ordner), push, dt.datetime.now(),
                         einzahlung_bestaetigt=args.einzahlung_bestaetigt)
    finally:
        konto.trennen()
    print(ergebnis.text)
    return 0 if ergebnis.ok else 1


if __name__ == "__main__":
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(errors="replace")
    sys.exit(main())
