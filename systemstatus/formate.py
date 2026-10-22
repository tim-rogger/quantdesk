"""Lauf-Protokolle verschiedener Bots in eine gemeinsame Form bringen.

bewerten.py kennt nur eine Form: ein Status-dict und eine Liste von Läufen {"ts", "ok", "errors"}.
Schreibt ein Bot sein Protokoll anders, übersetzt eine Funktion hier. In der Registry (deploy/bots.toml)
steht dann `format = "..."`. Ein weiterer Bot im selben Format braucht wieder nur einen Registry-Eintrag.

Reine Funktionen, keine Dateien – messen.py liest, hier wird nur übersetzt.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo


def zeit_lesen(text, zone_wenn_fehlt: str | None) -> float | None:
    """ISO-Zeit -> Unix-Sekunden. Ohne Zeitzone nur, wenn die Registry sagt, welche gemeint ist."""
    if not isinstance(text, str):
        return None
    try:
        zeitpunkt = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if zeitpunkt.tzinfo is None:
        if not zone_wenn_fehlt:
            return None
        zeitpunkt = zeitpunkt.replace(tzinfo=ZoneInfo(zone_wenn_fehlt))
    return zeitpunkt.timestamp()


# ====================================================================== Lotse
# Lotse schreibt pro Lauf mehrere Zeilen mit derselben Lauf-Nummer in laeufe.jsonl:
#     start  (zeit, modus)          – immer
#     kurse  (Kurs, Datum, Quelle)  – sobald die Kurse geholt sind
#     kasse  (investiert)           – nur wenn der Lauf bis zum Schluss durchkam
#     ende   (ergebnis-Text)        – fast immer; fehlt, wenn der Prozess abgestürzt oder abgebrochen ist
# In orders.jsonl steht jede Order zuerst als "notiert", danach ihr nächster Zustand (vorschlag, gesendet, …).

ORDER_ZUSTAND = {
    "notiert": "notiert, Stand unklar",
    "vorschlag": "nur Vorschlag – nicht gesendet",
    "gesendet": "gesendet – noch nicht ausgeführt",
    "ausgefuehrt": "ausgeführt",
    "storniert": "storniert",
    "verworfen": "verworfen",
}
SEITE = {"KAUF": "Kauf", "VERKAUF": "Verkauf"}


def lotse_tagebuch(eintraege: list[dict], ausfuehrungen: list[dict], orders: list[dict], jetzt: float,
                   zone_wenn_fehlt: str | None, laeuft_hoechstens_minuten: float,
                   budget: float | None = None) -> tuple[dict, list[dict]]:
    """Lotses Tagebuch -> (Status, Läufe) in der gemeinsamen Form, die bewerten.py versteht.

    ok      = es gibt eine kasse-Zeile zu diesem Lauf (genau dann schreibt Lotse sie)
    Fehler  = erste Zeile des ergebnis-Texts, wenn nicht ok; "ohne Ende" bei start ohne ende
    Ein Lauf, der gerade erst gestartet ist (jünger als `laeuft_hoechstens_minuten`), zählt noch nicht.
    """
    laeufe = _laeufe_sammeln(eintraege, zone_wenn_fehlt)
    fertige = _fertige_laeufe(laeufe, jetzt, laeuft_hoechstens_minuten)
    letzter = laeufe.get(fertige[-1]["lauf"], {}) if fertige else {}
    status = {
        "modus": _letzter_wert(eintraege, "start", "modus"),
        "investiert": _letzter_wert(eintraege, "kasse", "investiert"),
        "budget": budget,
        "positionen": _positionen(ausfuehrungen),
        "vorschlaege": _vorschlaege(orders, fertige[-1]["lauf"], letzter) if fertige else [],
        "kurse": _kurse(letzter.get("kurse") or {}),
        "verlauf": _gesamt_verlauf(laeufe),
    }
    status["wert"] = status["verlauf"][-1] if status["verlauf"] else None
    return status, fertige


def _laeufe_sammeln(eintraege: list[dict], zone_wenn_fehlt: str | None) -> dict[int, dict]:
    """Alle Zeilen nach Lauf-Nummer zusammenfassen."""
    laeufe: dict[int, dict] = {}
    for e in eintraege:
        nummer = e.get("lauf")
        if not isinstance(nummer, int):
            continue
        lauf = laeufe.setdefault(nummer, {})
        ereignis = e.get("ereignis")
        if ereignis == "start":
            lauf["start"] = zeit_lesen(e.get("zeit"), zone_wenn_fehlt)
            lauf["hat_start"] = True
        elif ereignis == "kurse":
            lauf["kurse"] = e.get("kurse")
        elif ereignis == "kasse":
            lauf["kasse"] = True
        elif ereignis == "ende":
            lauf["ende"] = True
            lauf["ergebnis"] = e.get("ergebnis") or ""
            lauf["gesamt"] = e.get("gesamt")
    return laeufe


def _fertige_laeufe(laeufe: dict[int, dict], jetzt: float, laeuft_hoechstens_minuten: float) -> list[dict]:
    """Läufe in der gemeinsamen Form {ts, ok, errors}. Laufende Läufe fehlen noch."""
    fertige = []
    for nummer in sorted(laeufe):
        lauf = laeufe[nummer]
        if not lauf.get("hat_start"):
            continue  # nur kasse/ende ohne start – kaputtes Protokoll, nicht raten
        start = lauf.get("start")
        if not lauf.get("ende"):
            if start is not None and jetzt - start < laeuft_hoechstens_minuten * 60:
                continue  # läuft gerade noch
            fertige.append({"ts": start, "ok": False, "errors": ["Lauf ohne Ende (abgestürzt oder abgebrochen)"],
                            "lauf": nummer})
            continue
        ok = bool(lauf.get("kasse"))
        grund = lauf["ergebnis"].splitlines()[0] if lauf["ergebnis"] else "ohne Grund"
        fertige.append({"ts": start, "ok": ok, "errors": [] if ok else [grund], "lauf": nummer})
    return fertige


def _gesamt_verlauf(laeufe: dict[int, dict]) -> list[float]:
    """Lotses Depotwert ("gesamt" in der ende-Zeile) Lauf für Lauf – für Wert und Mini-Kurve."""
    werte = [laeufe[n].get("gesamt") for n in sorted(laeufe)]
    return [w for w in werte if isinstance(w, (int, float)) and not isinstance(w, bool)][-90:]


def _letzter_wert(eintraege: list[dict], ereignis: str, feld: str):
    werte = [e.get(feld) for e in eintraege if e.get("ereignis") == ereignis]
    return werte[-1] if werte else None


def _positionen(ausfuehrungen: list[dict]) -> int:
    """Anzahl Papiere, von denen Lotse selbst Stück hält (Käufe minus Verkäufe)."""
    stueck: dict[str, float] = {}
    for a in ausfuehrungen:
        menge = a.get("menge")
        if isinstance(menge, (int, float)) and not isinstance(menge, bool):
            vorzeichen = 1 if a.get("seite") == "KAUF" else -1
            stueck[a.get("papier")] = stueck.get(a.get("papier"), 0.0) + vorzeichen * menge
    return sum(1 for s in stueck.values() if s > 1e-9)


def _vorschlaege(orders: list[dict], nummer: int, lauf: dict) -> list[str]:
    """Die Orders des letzten Laufs im Klartext. Ohne Orders: die erste Zeile des Ergebnisses."""
    zustand = {}
    for o in orders:
        zustand[o.get("ref")] = o.get("ereignis")
    texte = []
    for o in orders:
        if o.get("ereignis") == "notiert" and o.get("lauf") == nummer:
            texte.append(_order_text(o, zustand.get(o.get("ref"), "notiert")))
    if texte:
        return texte
    ergebnis = lauf.get("ergebnis") or ""
    return [ergebnis.splitlines()[0]] if ergebnis and lauf.get("kasse") else []


def _order_text(order: dict, zustand: str) -> str:
    """{"seite": "KAUF", "papier": "SSAC", "stueck": 2.8125, "betrag": 294.1} -> "Kauf SSAC 2.8125 Stück, ~294 CHF – …"."""
    seite = SEITE.get(order.get("seite"), str(order.get("seite")))
    stueck = _zahl(order.get("stueck"), 4)
    betrag = _zahl(order.get("betrag"), 0)
    return f"{seite} {order.get('papier')} {stueck} Stück, ~{betrag} CHF – {ORDER_ZUSTAND.get(zustand, zustand)}"


def _kurse(kurse: dict) -> list[str]:
    """{"SSAC": {"wert": 104.5, "waehrung": "CHF", "quelle": "yahoo", "datum": "2026-10-09"}} -> Klartext je Papier."""
    texte = []
    for papier, k in kurse.items():
        if not isinstance(k, dict):
            continue
        datum = k.get("datum") or ""
        tag = f"{datum[8:10]}.{datum[5:7]}." if len(datum) >= 10 else "ohne Datum"
        quelle = str(k.get("quelle") or "unbekannt").capitalize()
        texte.append(f"{papier} {_zahl(k.get('wert'), 2)} {k.get('waehrung') or ''} – Kurs von {quelle}, {tag}")
    return texte


def _zahl(wert, stellen: int) -> str:
    """2.81250 -> "2.8125"; fehlend -> "?"."""
    if not isinstance(wert, (int, float)) or isinstance(wert, bool):
        return "?"
    text = f"{wert:.{stellen}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text

