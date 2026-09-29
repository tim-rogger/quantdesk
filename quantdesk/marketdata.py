"""Letzter Preis: IBKR-Snapshot, Fallback Stooq (letzter Schlusskurs), dann Yahoo-Chart.

Stooq blockiert automatisierte Abrufe inzwischen oft mit einem JavaScript-Browsercheck –
deshalb gibt es Yahoo als zweiten Fallback. Die GUI zeigt an, welche Quelle genutzt wurde.
"""
from __future__ import annotations

import csv
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


def parse_yahoo_chart(data: dict) -> float | None:
    try:
        price = float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return price if price > 0 else None


class MarketData:
    def __init__(
        self,
        broker: Broker | None = None,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = 10.0,
    ):
        self.broker = broker
        self._session = session or requests.Session()
        self._clock = clock
        self._timeout = timeout
        self._cache: dict[tuple[str, str], tuple[float, float]] = {}  # (Quelle, Symbol) -> (Preis, Zeit)
        self._miss: dict[tuple[str, str], float] = {}  # (Quelle, Symbol) -> Zeit des Fehlschlags
        self._lock = threading.Lock()

    def get_quote(self, symbol: str) -> Quote | None:
        symbol = symbol.upper()
        for source, fetch, cache in (
            ("IBKR", self._ibkr, False),  # IBKR nie cachen: live, wenn verfügbar
            ("Stooq", self._stooq, True),
            ("Yahoo", self._yahoo, True),
        ):
            price = self._try(source, symbol, fetch, cache)
            if price is not None:
                return Quote(symbol, price, source)
        return None

    def get_price(self, symbol: str) -> float | None:
        quote = self.get_quote(symbol)
        return quote.price if quote else None

    def _try(self, source: str, symbol: str, fetch: Callable[[str], float | None], cache: bool) -> float | None:
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

    def _ibkr(self, symbol: str) -> float | None:
        if self.broker is None:
            return None
        return self.broker.get_last_price(symbol)

    def _stooq(self, symbol: str) -> float | None:
        resp = self._session.get(STOOQ_URL.format(symbol=stooq_symbol(symbol)), headers=HEADERS, timeout=self._timeout)
        resp.raise_for_status()
        return parse_stooq_csv(resp.text)

    def _yahoo(self, symbol: str) -> float | None:
        resp = self._session.get(YAHOO_CHART_URL.format(symbol=yahoo_symbol(symbol)), headers=HEADERS, timeout=self._timeout)
        resp.raise_for_status()
        return parse_yahoo_chart(resp.json())
