"""Letzter Preis: IBKR-Snapshot, Fallback Stooq (letzter Schlusskurs), dann Yahoo-Chart.

Stooq blockiert automatisierte Abrufe inzwischen oft mit einem JavaScript-Browsercheck –
deshalb gibt es Yahoo als zweiten Fallback. Die GUI zeigt an, welche Quelle genutzt wurde.

Für Papiere ausserhalb der USA (z.B. SIX) kann je Quelle ein eigenes Symbol gesetzt werden
(`symbole={"SSAC": {"stooq": "ssac.ch", "yahoo": "SSAC.SW"}}`); ohne Eintrag gilt wie bisher
stooq_symbol()/yahoo_symbol() (US-Aktien, Bot C). Ein Kurs trägt – wenn die Quelle es sagt – sein
Datum und seine Währung mit.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

import requests

from quantdesk.broker.base import Broker, BrokerError
from quantdesk.config import STOOQ_URL, YAHOO_CHART_URL

log = logging.getLogger(__name__)

CACHE_SECONDS = 300  # Tages-/Fallback-Daten nicht alle paar Sekunden abfragen
MISS_SECONDS = 300  # nach einem Fehlschlag die Quelle so lange überspringen
HEADERS = {"User-Agent": "Mozilla/5.0 (QuantDesk AI Bot)"}


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    source: str  # "IBKR", "Stooq" oder "Yahoo"
    datum: dt.date | None = None  # Handelstag des Kurses, wenn die Quelle ihn nennt
    waehrung: str | None = None  # Währung, wenn die Quelle sie nennt (Stooq nennt keine)


@dataclass(frozen=True)
class _Preis:
    wert: float
    datum: dt.date | None = None
    waehrung: str | None = None


def stooq_symbol(symbol: str) -> str:
    """AAPL -> aapl.us, BRK.B -> brk-b.us, aapl.us bleibt."""
    s = symbol.strip().lower()
    if s.endswith(".us"):
        return s
    return s.replace(".", "-") + ".us"


def yahoo_symbol(symbol: str) -> str:
    """BRK.B -> BRK-B"""
    return symbol.strip().upper().replace(".", "-")


def parse_stooq_csv(text: str) -> float | None:
    """Letzten Close aus Stooq-CSV (Date,Open,High,Low,Close,Volume)."""
    rows = list(csv.DictReader(io.StringIO(text.strip())))
    for row in reversed(rows):
        try:
            price = float(row.get("Close") or "")
        except ValueError:
            continue
        if price > 0:
            return price
    return None


def parse_stooq_last(text: str) -> tuple[float, dt.date | None] | None:
    """Letzter Close aus Stooq-CSV mit seinem Datum (Spalte Date, JJJJ-MM-TT)."""
    rows = list(csv.DictReader(io.StringIO(text.strip())))
    for row in reversed(rows):
        try:
            price = float(row.get("Close") or "")
        except ValueError:
            continue
        if price > 0:
            try:
                datum = dt.date.fromisoformat((row.get("Date") or "").strip())
            except ValueError:
                datum = None
            return price, datum
    return None


def parse_yahoo_chart(data: dict) -> float | None:
    try:
        price = float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return price if price > 0 else None


def parse_yahoo_quote(data: dict) -> tuple[float, dt.date | None, str | None] | None:
    """Preis wie parse_yahoo_chart, dazu Handelstag (regularMarketTime in Börsenzeit) und Währung."""
    price = parse_yahoo_chart(data)
    if price is None:
        return None
    meta = data["chart"]["result"][0]["meta"]
    zeit = meta.get("regularMarketTime")
    datum = None
    if isinstance(zeit, (int, float)):
        datum = (dt.datetime(1970, 1, 1) + dt.timedelta(seconds=zeit + (meta.get("gmtoffset") or 0))).date()
    return price, datum, meta.get("currency")


class MarketData:
    def __init__(
        self,
        broker: Broker | None = None,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = 10.0,
        symbole: dict[str, dict[str, str]] | None = None,
    ):
        self.broker = broker
        self._symbole = {k.upper(): v for k, v in (symbole or {}).items()}  # je Papier: {"stooq": …, "yahoo": …}
        self._session = session or requests.Session()
        self._clock = clock
        self._timeout = timeout
        self._cache: dict[tuple[str, str], tuple[_Preis, float]] = {}  # (Quelle, Symbol) -> (Preis, Zeit)
        self._miss: dict[tuple[str, str], float] = {}  # (Quelle, Symbol) -> Zeit des Fehlschlags
        self._lock = threading.Lock()

    def get_quote(self, symbol: str) -> Quote | None:
        symbol = symbol.upper()
        for source, fetch, cache in (
            ("IBKR", self._ibkr, False),  # IBKR nie cachen: live, wenn verfügbar
            ("Stooq", self._stooq, True),
            ("Yahoo", self._yahoo, True),
        ):
            preis = self._try(source, symbol, fetch, cache)
            if preis is not None:
                return Quote(symbol, preis.wert, source, preis.datum, preis.waehrung)
        return None

    def get_price(self, symbol: str) -> float | None:
        quote = self.get_quote(symbol)
        return quote.price if quote else None

    def _try(self, source: str, symbol: str, fetch: Callable[[str], _Preis | None], cache: bool) -> _Preis | None:
        key, now = (source, symbol), self._clock()
        with self._lock:
            cached = self._cache.get(key)
            miss = self._miss.get(key)
        if cached and now - cached[1] < CACHE_SECONDS:
            return cached[0]
        if miss is not None and now - miss < MISS_SECONDS:
            return None
        try:
            price = fetch(symbol)
        except (BrokerError, requests.RequestException, ValueError, csv.Error) as e:
            log.debug("%s-Preis für %s fehlgeschlagen: %s", source, symbol, e)
            price = None
        with self._lock:
            if price is None:
                self._miss[key] = now
            else:
                self._miss.pop(key, None)
                if cache:
                    self._cache[key] = (price, now)
        return price

    def _quell_symbol(self, quelle: str, symbol: str, standard: Callable[[str], str]) -> str | None:
        """Symbol für eine Quelle: aus dem Mapping, sonst Standard. Mapping-Eintrag "" = Quelle überspringen."""
        eigene = self._symbole.get(symbol)
        if eigene is not None and quelle in eigene:
            return eigene[quelle] or None
        return standard(symbol)

    def _ibkr(self, symbol: str) -> _Preis | None:
        if self.broker is None:
            return None
        price = self.broker.get_last_price(symbol)
        return _Preis(price) if price is not None else None

    def _stooq(self, symbol: str) -> _Preis | None:
        quell_symbol = self._quell_symbol("stooq", symbol, stooq_symbol)
        if quell_symbol is None:
            return None
        resp = self._session.get(STOOQ_URL.format(symbol=quell_symbol), headers=HEADERS, timeout=self._timeout)
        resp.raise_for_status()
        letzter = parse_stooq_last(resp.text)
        return _Preis(letzter[0], letzter[1]) if letzter else None

    def _yahoo(self, symbol: str) -> _Preis | None:
        quell_symbol = self._quell_symbol("yahoo", symbol, yahoo_symbol)
        if quell_symbol is None:
            return None
        resp = self._session.get(YAHOO_CHART_URL.format(symbol=quell_symbol), headers=HEADERS, timeout=self._timeout)
        resp.raise_for_status()
        kurs = parse_yahoo_quote(resp.json())
        return _Preis(*kurs) if kurs else None
