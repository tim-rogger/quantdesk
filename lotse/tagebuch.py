"""Lotse – Tagebuch: Lauf-Nummern, Order-Journal und der eigene Stück-Zähler (Regel 1, 15, 19).

Alles wird nur angehängt, nie überschrieben (JSON Lines, eine Zeile pro Ereignis):
  laeufe.jsonl         Start und Ende jedes Laufs, mit dem Inhalt der Zieldatei (Regel 1)
  orders.jsonl         jede Order VOR dem Senden notiert, danach abgehakt (Regel 19)
  ausfuehrungen.jsonl  vom Broker gemeldete Ausführungen – nur sie erhöhen den Zähler (Regel 15)
  gebuehren.jsonl      Kommission je Ausführung (kann später kommen als die Ausführung)
  dividenden.jsonl     Ausschüttungen eigener Papiere (Lotse-Anteil) – senken "investiert"

Zustände einer Order: notiert → gesendet → ausgefuehrt | storniert | verworfen; oder notiert → vorschlag.
Eine notierte, aber nie bestätigte Order wird nie ein zweites Mal gesendet: Der nächste Lauf gleicht sie mit
dem Broker ab (lauf.py) und schliesst sie.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os

log = logging.getLogger(__name__)

NOTIERT = "notiert"
GESENDET = "gesendet"
VORSCHLAG = "vorschlag"
AUSGEFUEHRT = "ausgefuehrt"
STORNIERT = "storniert"
VERWORFEN = "verworfen"
ABGESCHLOSSEN = (VORSCHLAG, AUSGEFUEHRT, STORNIERT, VERWORFEN)
REF_PREFIX = "lotse-"
GROESSTE_MENGE = 1e7  # mehr Stück sind nie echt – z.B. ib_async-Platzhalter UNSET_DOUBLE = 1.8e308


def gueltige_menge(menge) -> bool:
    """Eine vom Broker gemeldete Menge ist nur gültig, wenn sie eine endliche Zahl zwischen 0 und 10 Mio. ist."""
    if isinstance(menge, bool) or not isinstance(menge, (int, float)):
        return False
    return math.isfinite(menge) and 0 < menge < GROESSTE_MENGE


def gueltige_gebuehr(gebuehr) -> bool:
    """Eine Kommission ist gültig, wenn sie eine endliche Zahl ab 0 ist (0 ist erlaubt, negativ nicht)."""
    if isinstance(gebuehr, bool) or not isinstance(gebuehr, (int, float)):
        return False
    return math.isfinite(gebuehr) and 0 <= gebuehr < GROESSTE_MENGE


def gueltiger_betrag(betrag) -> bool:
    """Ein Geldbetrag ist gültig, wenn er eine endliche Zahl ist (negativ erlaubt, z.B. Quellensteuer)."""
    if isinstance(betrag, bool) or not isinstance(betrag, (int, float)):
        return False
    return math.isfinite(betrag) and abs(betrag) < GROESSTE_MENGE


class Tagebuch:
    def __init__(self, ordner: str):
        self.ordner = ordner
        os.makedirs(ordner, exist_ok=True)
        self._laeufe = os.path.join(ordner, "laeufe.jsonl")
        self._orders = os.path.join(ordner, "orders.jsonl")
        self._ausfuehrungen = os.path.join(ordner, "ausfuehrungen.jsonl")
        self._gebuehren = os.path.join(ordner, "gebuehren.jsonl")
        self._dividenden = os.path.join(ordner, "dividenden.jsonl")

    # ------------------------------------------------------------------ Dateien
    def _lies(self, pfad: str) -> list[dict]:
        if not os.path.exists(pfad):
            return []
        with open(pfad, encoding="utf-8") as f:
            return [json.loads(zeile) for zeile in f if zeile.strip()]

    def _schreib(self, pfad: str, eintrag: dict) -> None:
        zeile = json.dumps(eintrag, ensure_ascii=False, allow_nan=False)
        with open(pfad, "a", encoding="utf-8") as f:
            f.write(zeile + "\n")
            f.flush()
            os.fsync(f.fileno())  # erst auf der Platte, dann weiter (Absturz zwischen Notieren und Senden)

    # ------------------------------------------------------------------ Läufe (Regel 1, 19)
    def neuer_lauf(self, zieldatei: str, jetzt: dt.datetime, modus: str) -> int:
        """Neue Lauf-Nummer vergeben und den Inhalt der Zieldatei festhalten."""
        nummer = 1 + max((e["lauf"] for e in self._lies(self._laeufe)), default=0)
        self._schreib(self._laeufe, {"lauf": nummer, "ereignis": "start", "zeit": jetzt.isoformat(),
                                     "modus": modus, "zieldatei": zieldatei})
        return nummer

    def lauf_ende(self, nummer: int, ergebnis: str, jetzt: dt.datetime, gesamt: float | None = None) -> None:
        self._schreib(self._laeufe, {"lauf": nummer, "ereignis": "ende", "zeit": jetzt.isoformat(),
                                     "ergebnis": ergebnis, "gesamt": gesamt})

    def mindestdepot_erreicht(self) -> bool:
        """Wurde schon einmal vermerkt, dass das Depot das Mindestdepot erreicht hat? (Regel 12)"""
        return any(e["ereignis"] == "mindestdepot_erreicht" for e in self._lies(self._laeufe))

    def vermerke_mindestdepot(self, nummer: int, gesamt: float, jetzt: dt.datetime) -> bool:
        """Einmal vermerken, dass das Depot das Mindestdepot erreicht hat. Einmal gesetzt, nie wieder."""
        if self.mindestdepot_erreicht():
            return False
        self._schreib(self._laeufe, {"lauf": nummer, "ereignis": "mindestdepot_erreicht", "zeit": jetzt.isoformat(),
                                     "gesamt": gesamt})
        return True

    def vermerke_kasse(self, nummer: int, cash: float, investiert: float, jetzt: dt.datetime) -> None:
        """Cash und "investiert" eines erfolgreichen Laufs – Ausgangspunkt der Einzahlungsprüfung (Regel 0)."""
        self._schreib(self._laeufe, {"lauf": nummer, "ereignis": "kasse", "zeit": jetzt.isoformat(), "cash": cash,
                                     "investiert": investiert})

    def kasse_letzter_lauf(self) -> tuple[float, float] | None:
        """(Cash, investiert) beim letzten erfolgreichen Lauf. None = es gab noch keinen."""
        kassen = [e for e in self._lies(self._laeufe) if e["ereignis"] == "kasse"]
        return (kassen[-1]["cash"], kassen[-1]["investiert"]) if kassen else None

    def vermerke_bestaetigung(self, nummer: int, jetzt: dt.datetime) -> None:
        """Tim hat eine grosse Einzahlung für diesen Lauf bestätigt."""
        self._schreib(self._laeufe, {"lauf": nummer, "ereignis": "einzahlung_bestaetigt", "zeit": jetzt.isoformat()})

    def gesamt_letzter_lauf(self) -> float | None:
        """Depotwert des letzten beendeten Laufs, der einen Wert hatte (für Regel 12)."""
        werte = [e["gesamt"] for e in self._lies(self._laeufe)
                 if e["ereignis"] == "ende" and e.get("gesamt") is not None]
        return werte[-1] if werte else None

    # ------------------------------------------------------------------ Orders (Regel 19)
    def notiere(self, lauf: int, nummer: int, papier: str, seite: str, betrag: float, stueck: float,
                limit: float, jetzt: dt.datetime) -> str:
        """Order VOR dem Senden notieren. Liefert die Order-Referenz, die auch beim Broker steht."""
        ref = f"{REF_PREFIX}{lauf}-{nummer}"
        if ref in self.zustaende():
            raise ValueError(f"Order {ref} ist schon notiert – nie zweimal")
        self._schreib(self._orders, {"ref": ref, "ereignis": NOTIERT, "zeit": jetzt.isoformat(), "lauf": lauf,
                                     "papier": papier, "seite": seite, "betrag": betrag, "stueck": stueck,
                                     "limit": limit})
        return ref

    def abhaken(self, ref: str, ereignis: str, jetzt: dt.datetime, **details) -> None:
        """Nächsten Zustand einer Order festhalten (gesendet, vorschlag, ausgefuehrt, storniert, verworfen)."""
        if ref not in self.zustaende():
            raise KeyError(f"Order {ref} ist nicht notiert")
        self._schreib(self._orders, {"ref": ref, "ereignis": ereignis, "zeit": jetzt.isoformat(), **details})

    def zustaende(self) -> dict[str, str]:
        """Letzter Zustand je Order-Referenz."""
        out: dict[str, str] = {}
        for e in self._lies(self._orders):
            out[e["ref"]] = e["ereignis"]
        return out

    def order(self, ref: str) -> dict:
        """Die notierten Angaben einer Order (Papier, Seite, Betrag, Stück, Limit)."""
        for e in self._lies(self._orders):
            if e["ref"] == ref and e["ereignis"] == NOTIERT:
                return e
        raise KeyError(ref)

    def unbestaetigt(self) -> list[str]:
        """Notiert, aber nie als gesendet bestätigt – z.B. Absturz zwischen Notieren und Senden."""
        return [ref for ref, z in self.zustaende().items() if z == NOTIERT]

    def gesendet_offen(self) -> list[str]:
        """Gesendet, aber noch nicht abgeschlossen."""
        return [ref for ref, z in self.zustaende().items() if z == GESENDET]

    def heute(self, tag: dt.date) -> tuple[float, int]:
        """Heute schon bestellt (CHF, Anzahl) – alle notierten Orders ausser reinen Vorschlägen (für Regel 18)."""
        zustand = self.zustaende()
        betrag, anzahl = 0.0, 0
        for e in self._lies(self._orders):
            if e["ereignis"] == NOTIERT and e["zeit"][:10] == tag.isoformat() and zustand[e["ref"]] != VORSCHLAG:
                betrag += e["betrag"]
                anzahl += 1
        return betrag, anzahl

    # ------------------------------------------------------------------ eigener Zähler (Regel 15)
    def buche_ausfuehrung(self, ref: str, exec_id: str, papier: str, seite: str, menge, preis,
                          jetzt: dt.datetime, gebuehr=None, ausgefuehrt: dt.datetime | None = None) -> bool:
        """Eine vom Broker gemeldete Ausführung buchen. Nur die AUSGEFÜHRTE Menge zählt, nie die Bestellmenge.

        Liefert False (und bucht nichts), wenn die Menge ungültig ist (nan, inf, 0, negativ, Platzhalter
        1.8e308), die Order nicht von Lotse stammt oder die Ausführung schon gebucht ist.
        """
        if not gueltige_menge(menge):
            log.error("Ausführung %s: ungültige Menge %r – nicht gebucht", exec_id, menge)
            return False
        if not gueltige_menge(preis):
            log.error("Ausführung %s: ungültiger Preis %r – nicht gebucht", exec_id, preis)
            return False
        if ref not in self.zustaende():
            log.warning("Ausführung %s gehört zu keiner Lotse-Order (%s) – nicht gebucht", exec_id, ref)
            return False
        if any(e["exec_id"] == exec_id for e in self._lies(self._ausfuehrungen)):
            return False
        if seite == "VERKAUF" and menge > self.eigene_stueck().get(papier, 0.0) + 1e-9:
            log.error("Ausführung %s: Verkauf von %s Stück %s, Lotse besitzt weniger – nicht gebucht",
                      exec_id, menge, papier)
            return False
        self._schreib(self._ausfuehrungen, {"exec_id": exec_id, "ref": ref, "papier": papier, "seite": seite,
                                            "menge": float(menge), "preis": float(preis),
                                            "zeit": jetzt.isoformat(),
                                            "ausgefuehrt": (ausgefuehrt or jetzt).isoformat()})
        if gebuehr is not None:
            self.buche_gebuehr(exec_id, gebuehr, jetzt)
        return True

    def buche_gebuehr(self, exec_id: str, gebuehr, jetzt: dt.datetime) -> bool:
        """Kommission einer gebuchten Ausführung festhalten – einmal je Ausführung, nur gültige Beträge."""
        if not gueltige_gebuehr(gebuehr):
            log.error("Gebühr %r zu Ausführung %s ungültig – nicht gebucht", gebuehr, exec_id)
            return False
        if not any(e["exec_id"] == exec_id for e in self._lies(self._ausfuehrungen)):
            return False
        if any(e["exec_id"] == exec_id for e in self._lies(self._gebuehren)):
            return False
        self._schreib(self._gebuehren, {"exec_id": exec_id, "gebuehr": float(gebuehr), "zeit": jetzt.isoformat()})
        return True

    def ohne_gebuehr(self) -> list[str]:
        """Ausführungen, zu denen IBKR noch keine Kommission gemeldet hat."""
        bekannt = {e["exec_id"] for e in self._lies(self._gebuehren)}
        return [e["exec_id"] for e in self._lies(self._ausfuehrungen) if e["exec_id"] not in bekannt]

    def investiert(self) -> float:
        """CHF, die Lotse netto verbraucht hat (für das Budget in Regel 7): Käufe (Menge × Preis) plus
        Kommission, minus Verkaufserlöse nach Kommission."""
        summe = 0.0
        for e in self._lies(self._ausfuehrungen):
            vorzeichen = 1 if e["seite"] == "KAUF" else -1
            summe += vorzeichen * e["menge"] * e["preis"]
        for e in self._lies(self._gebuehren):
            summe += e["gebuehr"]  # Kommission verbraucht Geld – beim Kauf wie beim Verkauf
        for e in self._lies(self._dividenden):
            summe -= e["lotse_betrag"]  # Ausschüttung = Cash, das aus der eigenen Position zurückkommt
        return summe

    # ------------------------------------------------------------------ Ausschüttungen
    def buche_dividende(self, gutschrift_id: str, papier: str, betrag, datum: dt.date | None, konto_stueck,
                        jetzt: dt.datetime) -> float | None:
        """Ausschüttung (oder Quellensteuer darauf, negativ) eines Papiers buchen – nur, wenn Lotse es am
        Zahltag selbst hielt. Gehören nicht alle Stück im Konto Lotse, zählt nur sein Anteil
        (eigene Stück / Stück im Konto). Liefert den gebuchten Lotse-Betrag oder None (nicht gebucht)."""
        if not gueltiger_betrag(betrag) or datum is None or not gutschrift_id:
            log.error("Gutschrift %s (%s): ungültig (%r, %r) – nicht gebucht", gutschrift_id, papier, betrag, datum)
            return None
        if any(e["id"] == gutschrift_id for e in self._lies(self._dividenden)):
            return None
        eigene = self.eigene_stueck(bis=datum).get(papier, 0.0)
        if eigene <= 0:
            return None  # Papier gehörte Lotse am Zahltag nicht – geht Lotse nichts an
        im_konto = konto_stueck if gueltige_menge(konto_stueck) else eigene
        anteil = min(eigene / max(im_konto, eigene), 1.0)
        lotse_betrag = betrag * anteil
        self._schreib(self._dividenden, {"id": gutschrift_id, "papier": papier, "betrag": float(betrag),
                                         "datum": datum.isoformat(), "anteil": anteil, "lotse_betrag": lotse_betrag,
                                         "zeit": jetzt.isoformat()})
        return lotse_betrag

    def dividenden(self) -> float:
        """Summe der gebuchten Ausschüttungen (Lotse-Anteil, nach Quellensteuer)."""
        return sum(e["lotse_betrag"] for e in self._lies(self._dividenden))

    def eigene_stueck(self, bis: dt.date | None = None) -> dict[str, float]:
        """Stück je Papier, die Lotse selbst gekauft (minus verkauft) hat – nur aus gebuchten Ausführungen.
        Mit `bis`: nur Ausführungen bis und mit diesem Tag (z.B. Zahltag einer Ausschüttung)."""
        out: dict[str, float] = {}
        for e in self._lies(self._ausfuehrungen):
            if bis is not None and (e.get("ausgefuehrt") or e["zeit"])[:10] > bis.isoformat():
                continue
            vorzeichen = 1 if e["seite"] == "KAUF" else -1
            out[e["papier"]] = out.get(e["papier"], 0.0) + vorzeichen * e["menge"]
        return {p: s for p, s in out.items() if abs(s) > 1e-12}
