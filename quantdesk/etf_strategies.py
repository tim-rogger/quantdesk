"""Kandidat D (Trendfolge über Anlageklassen) und die Vergleichsportfolios von Phase 2.

Alle Parameter stehen hier fest und entsprechen der Anmeldung research/anmeldungen/D.md. Sie werden nach
einem Lauf nicht verändert. Jede Änderung wäre ein neuer Versuch mit eigener Registerzeile.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from quantdesk.allocation import View
from quantdesk.etf_data import CORE_KEYS

LONG = 252  # langes Fenster: 12 Monate (Moskowitz/Ooi/Pedersen 2012)
SHORT = 42  # kurzes Fenster: 2 Monate (Barbell kurz + lang, keine mittlere Ebene)
TOP_N = 3  # D3: beste 3 nach kombiniertem Trend
VOL_WINDOW = 126  # Risikoparität: Schwankung der letzten 6 Monate


def _signal(view: View, sym: str, lookback: int) -> bool:
    """Trend positiv = Rendite über `lookback` Tage höher als der T-Bill-Zins im selben Zeitraum."""
    r = view.total_return(sym, lookback)
    return r is not None and r > view.cash_return(lookback)


@dataclass(frozen=True)
class TrendLong:
    """D1 – nur lang: je Anlageklasse 1/N investiert, wenn der 12-Monats-Trend positiv ist, sonst Cash."""
    universe: tuple[str, ...] = CORE_KEYS
    name: str = "D1 Trend lang (12 M)"

    def target_weights(self, view: View) -> dict[str, float]:
        avail = view.available(self.universe, LONG + 1)
        if not avail:
            return {}
        slot = 1 / len(avail)
        return {s: slot for s in avail if _signal(view, s, LONG)}


@dataclass(frozen=True)
class TrendBarbell:
    """D2 – kurz + lang: je Klasse 1/N × (Signal lang + Signal kurz)/2 → 0, halb oder ganz investiert."""
    universe: tuple[str, ...] = CORE_KEYS
    name: str = "D2 Trend kurz+lang (2 M + 12 M)"

    def target_weights(self, view: View) -> dict[str, float]:
        avail = view.available(self.universe, LONG + 1)
        if not avail:
            return {}
        slot = 1 / len(avail)
        out = {}
        for s in avail:
            share = (_signal(view, s, LONG) + _signal(view, s, SHORT)) / 2
            if share:
                out[s] = slot * share
        return out


@dataclass(frozen=True)
class TrendTopN:
    """D3 – kurz + lang mit Rangauswahl: Punktzahl = Ø der Überrenditen über 2 M und 12 M;
    die besten TOP_N mit positiver Punktzahl je 1/TOP_N, Rest Cash."""
    universe: tuple[str, ...] = CORE_KEYS
    top_n: int = TOP_N
    name: str = "D3 Trend kurz+lang, beste 3"

    def target_weights(self, view: View) -> dict[str, float]:
        scores = []
        for s in view.available(self.universe, LONG + 1):
            rl, rs = view.total_return(s, LONG), view.total_return(s, SHORT)
            if rl is None or rs is None:
                continue
            score = ((rl - view.cash_return(LONG)) + (rs - view.cash_return(SHORT))) / 2
            if score > 0:
                scores.append((score, s))
        best = sorted(scores, key=lambda x: (-x[0], x[1]))[: self.top_n]
        return {s: 1 / self.top_n for _, s in best}


# --------------------------------------------------------------------------- Vergleichsportfolios (keine Kandidaten)
@dataclass(frozen=True)
class Hold:
    """Ein ETF zu 100 % (z.B. SPY = Massstab)."""
    symbol: str
    name: str = ""

    def target_weights(self, view: View) -> dict[str, float]:
        return {self.symbol: 1.0}


@dataclass(frozen=True)
class FixedMix:
    """Feste Gewichte, monatlich zurückgesetzt (z.B. 60/40 SPY/IEF)."""
    weights: tuple[tuple[str, float], ...]
    name: str = ""

    def target_weights(self, view: View) -> dict[str, float]:
        return dict(self.weights)


@dataclass(frozen=True)
class EqualWeight:
    """Alle verfügbaren Anlageklassen gleich gewichtet, monatlich – Diversifikation ohne Trend."""
    universe: tuple[str, ...] = CORE_KEYS
    name: str = "Alle Klassen gleich gewichtet"

    def target_weights(self, view: View) -> dict[str, float]:
        avail = view.available(self.universe, LONG + 1)
        return {s: 1 / len(avail) for s in avail} if avail else {}


@dataclass(frozen=True)
class RiskParity:
    """G – Risikoparität (Massstab): Gewichte ∝ 1/Schwankung der letzten 6 Monate, voll investiert, ohne Hebel."""
    universe: tuple[str, ...] = CORE_KEYS
    name: str = "G Risikoparität (1/Vola, 6 M)"

    def target_weights(self, view: View) -> dict[str, float]:
        inv = {}
        for s in view.available(self.universe, LONG + 1):
            r = view.daily_returns(s, VOL_WINDOW)
            if r:
                sd = statistics.pstdev(r)
                if sd > 0 and math.isfinite(sd):
                    inv[s] = 1 / sd
        total = sum(inv.values())
        return {s: v / total for s, v in inv.items()} if total else {}


def candidates_d(universe: tuple[str, ...] = CORE_KEYS) -> list:
    return [TrendLong(universe), TrendBarbell(universe), TrendTopN(universe)]


def benchmarks(universe: tuple[str, ...] = CORE_KEYS) -> list:
    return [Hold("SPY", "SPY halten (Massstab)"), FixedMix((("SPY", 0.6), ("IEF", 0.4)), "60/40 SPY/IEF"),
            RiskParity(universe), EqualWeight(universe)]


# --------------------------------------------------------------------------- Kennzahl: Mischung SPY + Kandidat
MIX_SPY_SHARES = (0.9, 0.8, 0.7, 0.5)  # Tims Festlegung 09.10.2026 – Kennzahl, KEIN Bestehens-Kriterium


@dataclass(frozen=True)
class SpyBlend:
    """x % SPY + (1−x) % Kandidat, beide Zielgewichte zusammen monatlich zurückgesetzt (gleiche Kosten)."""
    inner: object
    spy_share: float

    @property
    def name(self) -> str:
        spy = round(self.spy_share * 100)
        return f"{spy} % SPY + {100 - spy} % {self.inner.name.split()[0]}"

    def target_weights(self, view: View) -> dict[str, float]:
        out = {s: (1 - self.spy_share) * w for s, w in self.inner.target_weights(view).items()}
        out["SPY"] = out.get("SPY", 0.0) + self.spy_share
        return out
