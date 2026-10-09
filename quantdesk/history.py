"""Historische Tageskurse (Yahoo-Chart, split-/dividendenbereinigt) mit lokalem Cache für den Backtester."""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass

import requests

from quantdesk.marketdata import HEADERS, yahoo_symbol

YAHOO_HISTORY_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?period1={start}&period2={end}&interval=1d&events=split,div"
)
CACHE_DIR = os.getenv("QUANTDESK_CACHE_DIR", ".cache")


@dataclass(frozen=True)
class Bar:
    day: str
    open: float
    high: float
    low: float
    close: float


def utc_day(ts: float) -> str:
    """Unix-Sekunden -> 'YYYY-MM-DD' (UTC), auch vor 1970 (time.gmtime scheitert dort unter Windows)."""
    return (dt.datetime(1970, 1, 1) + dt.timedelta(seconds=ts)).strftime("%Y-%m-%d")


def parse_yahoo_history(data: dict) -> list[Bar]:
    """OHLC mit adjclose/close skalieren, damit Splits und Dividenden keine Sprünge machen."""
    try:
        result = data["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        adj = result["indicators"]["adjclose"][0]["adjclose"]
        stamps = result["timestamp"]
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError("Unerwartete Antwort von Yahoo (Symbol falsch?)") from e
    bars = []
    for t, o, h, l, c, a in zip(stamps, quote["open"], quote["high"], quote["low"], quote["close"], adj):
        if None in (o, h, l, c, a) or c <= 0:
            continue
        f = a / c
        bars.append(Bar(utc_day(t), o * f, h * f, l * f, a))
    return bars


def load_history(
    symbol: str,
    years: int = 10,
    session: requests.Session | None = None,
    cache_dir: str | None = CACHE_DIR,
    now: float | None = None,
) -> list[Bar]:
    """Tagesbars der letzten `years` Jahre. Pro Tag wird höchstens einmal geladen (Cache)."""
    now = time.time() if now is None else now
    symbol = symbol.upper()
    cache_file = None
    if cache_dir:
        cache_file = os.path.join(cache_dir, f"{symbol}_{years}y_{time.strftime('%Y%m%d', time.gmtime(now))}.json")
        if os.path.exists(cache_file):
            with open(cache_file, encoding="utf-8") as f:
                return [Bar(*row) for row in json.load(f)]
    url = YAHOO_HISTORY_URL.format(symbol=yahoo_symbol(symbol), start=int(now - years * 365.25 * 86400), end=int(now))
    resp = (session or requests).get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    bars = parse_yahoo_history(resp.json())
    if not bars:
        raise ValueError(f"Keine Kursdaten für {symbol}")
    if cache_file:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump([[b.day, b.open, b.high, b.low, b.close] for b in bars], f)
    return bars
