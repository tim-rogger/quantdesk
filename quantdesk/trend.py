"""Trendfilter für den Live-Betrieb – dieselbe Regel wie im Backtest (backtest.trend_ok_series).

Heute ist Handel erlaubt, wenn der letzte Schlusskurs VOR heute über dem Durchschnitt der `sma`
Schlusskurse davor liegt. Der heutige (laufende) Tagesbar wird nie verwendet.
"""
from __future__ import annotations

import logging
import statistics
import time
from typing import Callable

import requests

from quantdesk.history import Bar, load_history

log = logging.getLogger(__name__)


def trend_ok_now(closes: list[float], sma: int) -> bool | None:
    """closes = Schlusskurse bis einschliesslich gestern. None = zu wenig Daten."""
    if len(closes) < sma + 1:
        return None
    return closes[-1] > statistics.fmean(closes[-1 - sma:-1])


class TrendFilter:
    """Pro Symbol und Tag einmal berechnet (Kursdaten kommen aus dem Tages-Cache von history.py)."""

    def __init__(
        self,
        loader: Callable[[str, int], list[Bar]] = load_history,
        clock: Callable[[], float] = time.time,
        years: int = 2,
    ):
        self._loader = loader
        self._clock = clock
        self._years = years
        self._cache: dict[tuple[str, int, str], bool | None] = {}

    def today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self._clock()))

    def __call__(self, symbol: str, sma: int) -> bool | None:
        today = self.today()
        key = (symbol.upper(), sma, today)
        if key not in self._cache:
            try:
                bars = self._loader(symbol, self._years)
            except (requests.RequestException, ValueError, OSError) as e:
                log.warning("Trend für %s nicht bestimmbar: %s", symbol, e)
                return None  # nicht cachen – nächste Runde neu versuchen
            self._cache[key] = trend_ok_now([b.close for b in bars if b.day < today], sma)
        return self._cache[key]
