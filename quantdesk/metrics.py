"""Kennzahlen für Kapitalkurven (ein Wert pro Handelstag) – für jede Strategie gleich berechnet."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

TRADING_DAYS = 252


@dataclass(frozen=True)
class Perf:
    total_return: float  # 0.25 = +25 %
    cagr: float  # Rendite pro Jahr (auf das ganze Budget)
    volatility: float  # Schwankung pro Jahr
    sharpe: float  # Überrendite über dem risikolosen Zins / Schwankung, pro Jahr
    max_drawdown: float  # grösster Rückgang vom Höchststand, z.B. -0.35
    avg_invested: float  # Ø investierter Anteil des Budgets (0..1)
    return_on_invested: float  # Rendite p.a. / Ø investierter Anteil ≈ Rendite, wenn immer voll investiert
    years: float


def max_drawdown(values: list[float]) -> float:
    peak, worst = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1)
    return worst


def add_cash_interest(values: list[float], invested: list[float], daily_rates: list[float]) -> list[float]:
    """Zinsen auf den nicht investierten Teil (values - invested) gutschreiben, Zinsen werden mitverzinst.

    daily_rates[i] = Zins für den Schritt von Tag i-1 auf Tag i (z.B. T-Bill-Jahreszins / 252).
    Näherung: die Handelsentscheidungen der Strategie hängen nicht vom Zinsertrag ab.
    """
    out, accrued = [values[0]], 0.0
    for i in range(1, len(values)):
        cash = max(values[i - 1] - invested[i - 1], 0.0) + accrued
        accrued += cash * daily_rates[i]
        out.append(values[i] + accrued)
    return out


def perf(values: list[float], invested: list[float] | None = None, rf: list[float] | None = None) -> Perf:
    """`values` = Kontowert (Cash + Positionen) pro Tag, `invested` = Wert der Positionen pro Tag,
    `rf` = risikoloser Tageszins pro Tag (für die Sharpe; None = 0)."""
    if len(values) < 2 or values[0] <= 0:
        raise ValueError("Mindestens 2 positive Werte nötig.")
    years = (len(values) - 1) / TRADING_DAYS
    total = values[-1] / values[0] - 1
    cagr = (max(values[-1], 0) / values[0]) ** (1 / years) - 1 if years > 0 else 0.0
    rf = rf or [0.0] * len(values)
    rets, excess = [], []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            r = values[i] / values[i - 1] - 1
            rets.append(r)
            excess.append(r - rf[i])
    vol = statistics.pstdev(rets) * math.sqrt(TRADING_DAYS) if len(rets) > 1 else 0.0
    ex_vol = statistics.pstdev(excess) * math.sqrt(TRADING_DAYS) if len(excess) > 1 else 0.0
    sharpe = statistics.fmean(excess) * TRADING_DAYS / ex_vol if ex_vol > 0 else 0.0
    if invested is None:
        avg_inv = 1.0
    else:
        avg_inv = statistics.fmean(i / v for i, v in zip(invested, values) if v > 0)
    roi = cagr / avg_inv if avg_inv > 0 else 0.0
    return Perf(total, cagr, vol, sharpe, max_drawdown(values), avg_inv, roi, years)
