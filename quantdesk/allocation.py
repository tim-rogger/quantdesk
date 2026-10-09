"""Strategie als Modul: Kurse bis heute rein, Zielgewichte raus – für Backtest und (später) Live-Bot gleich.

Eine Strategie sieht nur, was an ihrem Entscheidungstag bekannt ist (`View`: Schlusskurse bis und mit diesem
Tag, T-Bill-Zins). Sie liefert Zielgewichte je Symbol (Summe ≤ 1, Rest = Cash, long-only, kein Hebel).

Der Simulator (`simulate`) entscheidet am letzten Handelstag jedes Monats (Schlusskurs) und handelt am
nächsten Handelstag zum Schlusskurs (ein Tag Verzögerung, kein Blick in die Zukunft). Cash bekommt den
T-Bill-Zins des Vortags. Kosten: feste Kommission je Order plus Schlupf in Basispunkten des Handelsvolumens.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Callable, Protocol

from quantdesk.history import Bar

TRADING_DAYS = 252


@dataclass(frozen=True)
class Costs:
    commission: float = 1.0  # $ je Order
    slippage_bps: float = 5.0  # Spread + Markteinfluss, in Basispunkten des gehandelten Betrags


class Aligned:
    """Alle Reihen auf einem gemeinsamen Kalender; vor dem eigenen Start: nicht vorhanden (None)."""

    def __init__(self, bars: dict[str, list[Bar]], rates: list[tuple[str, float]], end: str | None = None):
        days = sorted({b.day for s in bars.values() for b in s})
        self.days = [d for d in days if end is None or d <= end]
        self.symbols = list(bars)
        self.close: dict[str, list[float | None]] = {}
        self.count: dict[str, list[int]] = {}  # Anzahl eigener Kurse bis und mit Tag i
        for sym, series in bars.items():
            by_day = {b.day: b.close for b in series}
            out, cnt, last, n = [], [], None, 0
            for d in self.days:
                if d in by_day:
                    last, n = by_day[d], n + 1
                out.append(last)
                cnt.append(n)
            self.close[sym], self.count[sym] = out, cnt
        keys, vals = [d for d, _ in rates], [v for _, v in rates]
        self.rate = []
        for d in self.days:
            k = bisect.bisect_right(keys, d) - 1
            self.rate.append(vals[k] if k >= 0 else (vals[0] if vals else 0.0))
        self.cash_index = [1.0]
        for i in range(1, len(self.days)):
            self.cash_index.append(self.cash_index[-1] * (1 + self.rate[i - 1] / TRADING_DAYS))

    def index(self, day: str) -> int:
        return bisect.bisect_left(self.days, day)


@dataclass(frozen=True)
class View:
    """Was eine Strategie am Tag `i` wissen darf. Zugriffe auf spätere Tage sind nicht möglich."""
    data: Aligned
    i: int

    @property
    def day(self) -> str:
        return self.data.days[self.i]

    def available(self, symbols: tuple[str, ...], min_history: int) -> list[str]:
        """Symbole mit mindestens `min_history` eigenen Kursen bis heute."""
        return [s for s in symbols if s in self.data.count and self.data.count[s][self.i] >= min_history]

    def total_return(self, sym: str, lookback: int) -> float | None:
        if self.data.count[sym][self.i] <= lookback or self.i < lookback:
            return None
        now, then = self.data.close[sym][self.i], self.data.close[sym][self.i - lookback]
        return None if not now or not then else now / then - 1

    def cash_return(self, lookback: int) -> float:
        j = max(self.i - lookback, 0)
        return self.data.cash_index[self.i] / self.data.cash_index[j] - 1

    def daily_returns(self, sym: str, lookback: int) -> list[float] | None:
        if self.data.count[sym][self.i] <= lookback or self.i < lookback:
            return None
        c = self.data.close[sym][self.i - lookback:self.i + 1]
        return [b / a - 1 for a, b in zip(c, c[1:])]


class Strategy(Protocol):
    name: str

    def target_weights(self, view: View) -> dict[str, float]: ...


def month_ends(days: list[str]) -> set[int]:
    """Indizes der letzten Handelstage jedes Monats."""
    return {i for i in range(len(days) - 1) if days[i][:7] != days[i + 1][:7]}


@dataclass
class SimResult:
    name: str
    days: list[str]
    values: list[float]  # Kontowert inkl. Cash-Zinsen
    invested: list[float]  # Wert der Positionen
    orders: int = 0
    costs: float = 0.0
    weights_log: list[tuple[str, dict[str, float]]] = field(default_factory=list)  # (Entscheidungstag, Ziel)


def _check_weights(w: dict[str, float], name: str) -> dict[str, float]:
    if any((not math.isfinite(x)) or x < -1e-12 for x in w.values()):
        raise ValueError(f"{name}: ungültige Gewichte {w} (long-only, endlich)")
    total = sum(w.values())
    if total > 1 + 1e-9:
        raise ValueError(f"{name}: Gewichte summieren sich auf {total:.4f} > 1 (kein Hebel)")
    return {k: v for k, v in w.items() if v > 1e-12}


def simulate(strategy: Strategy, data: Aligned, start: str, end: str | None = None, capital: float = 100_000.0,
             costs: Costs = Costs(), decide_on: Callable[[list[str]], set[int]] = month_ends) -> SimResult:
    """Strategie von `start` bis `end` laufen lassen. Die erste Entscheidung fällt am Starttag."""
    i0 = data.index(start)
    i1 = len(data.days) - 1 if end is None else bisect.bisect_right(data.days, end) - 1
    if i0 >= i1:
        raise ValueError("Zeitraum zu kurz.")
    decisions = {i for i in decide_on(data.days) if i0 <= i < i1} | {i0}
    cash, qty = capital, {}
    pending: dict[str, float] | None = None
    res = SimResult(strategy.name, [], [], [])
    for i in range(i0, i1 + 1):
        if i > i0:
            cash *= 1 + data.rate[i - 1] / TRADING_DAYS
        price = {s: data.close[s][i] for s in qty}
        if pending is not None:  # gestern entschieden -> heute zum Schlusskurs handeln
            px = {s: data.close[s][i] for s in set(pending) | set(qty)}
            value = cash + sum(q * px[s] for s, q in qty.items())
            for s in sorted(set(pending) | set(qty)):
                target_value = pending.get(s, 0.0) * value
                current = qty.get(s, 0.0) * px[s]
                trade = target_value - current
                if abs(trade) < 1e-6:
                    continue
                fee = costs.commission + abs(trade) * costs.slippage_bps / 10_000
                cash -= trade + fee
                res.orders += 1
                res.costs += fee
                qty[s] = target_value / px[s]
                if qty[s] <= 1e-12:
                    qty.pop(s)
            pending = None
            price = {s: data.close[s][i] for s in qty}
        invested = sum(q * price[s] for s, q in qty.items())
        res.days.append(data.days[i])
        res.values.append(cash + invested)
        res.invested.append(invested)
        if i in decisions and i < i1:
            w = _check_weights(strategy.target_weights(View(data, i)), strategy.name)
            missing = [s for s in w if data.close.get(s, [None])[i] is None]
            if missing:
                raise ValueError(f"{strategy.name}: Gewicht auf Symbol ohne Kurs: {missing}")
            res.weights_log.append((data.days[i], w))
            pending = w
    return res
