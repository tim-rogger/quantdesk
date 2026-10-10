"""Lotse – Kursquelle ausserhalb von IBKR: letzter Schlusskurs von Yahoo (Chart-API), mit Datum und Währung.

IBKR liefert für die SIX (EBS) ohne Marktdaten-Abo weder Live- noch historische Kurse (Error 354/162). Lotse
läuft monatlich und braucht nur den letzten Schlusskurs. Orders laufen weiterhin über IBKR an EBS.

Verwendet wird der echte Schlusskurs (`close`), nicht der um Dividenden bereinigte (`adjclose`) – für ein
Limit zählt der Preis, zu dem tatsächlich gehandelt wurde. Die Währung kommt mit; lauf.py stoppt (Regel 0),
wenn sie nicht CHF ist – es wird nie umgerechnet.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
KOPF = {"User-Agent": "Mozilla/5.0 (Lotse)"}


@dataclass(frozen=True)
class Notierung:
    """Letzter Schlusskurs, sein Handelstag und die Währung. Alles kann fehlen (None)."""
    wert: float | None
    datum: dt.date | None
    waehrung: str | None


KEINE = Notierung(None, None, None)


def lies_yahoo(daten: dict) -> Notierung:
    """Antwort der Yahoo-Chart-API -> jüngster Tagesbalken mit Schlusskurs (leere Balken werden übersprungen)."""
    try:
        ergebnis = daten["chart"]["result"][0]
        meta = ergebnis["meta"]
        zeiten = ergebnis.get("timestamp") or []
        schluesse = ergebnis["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        return KEINE
    versatz = meta.get("gmtoffset") or 0  # Sekunden; Handelstag in Börsenzeit (Zürich)
    for zeit, schluss in reversed(list(zip(zeiten, schluesse))):
        if isinstance(schluss, (int, float)) and math.isfinite(schluss):
            tag = (dt.datetime(1970, 1, 1) + dt.timedelta(seconds=zeit + versatz)).date()
            return Notierung(float(schluss), tag, meta.get("currency"))
    return Notierung(None, None, meta.get("currency"))


class YahooKurse:
    def __init__(self, session=None, zeitlimit: float = 20.0):
        self._session = session or requests
        self.zeitlimit = zeitlimit

    def notierung(self, symbol: str) -> Notierung:
        """Letzter Schlusskurs eines Symbols (z.B. "SSAC.SW"). Netz- oder Formatfehler -> leere Notierung."""
        try:
            antwort = self._session.get(YAHOO_URL.format(symbol=symbol), headers=KOPF, timeout=self.zeitlimit)
            antwort.raise_for_status()
            return lies_yahoo(antwort.json())
        except (requests.RequestException, ValueError) as fehler:
            log.warning("Yahoo-Kurs %s nicht lesbar: %s", symbol, fehler)
            return KEINE
