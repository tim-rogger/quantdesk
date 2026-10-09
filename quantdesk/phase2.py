"""Auswertung Forschungsphase 2: Kennzahlen USD/CHF, Testjahre, Bestehen-Regel (Sicht 1 + DSR), Sicht 2
(risiko-normiert), Korrelationen. Grundlage: SimResult aus quantdesk.allocation.

Sicht 1 (risikobereinigt, wie Phase 1, Massstab SPY halten):
  K1 Sharpe ≥ SPY + 0.2 · K2 max DD nicht schlimmer · K3 in ≥ 2/3 der Testjahre höhere Sharpe ·
  K4 mehr Rendite als SPY + T-Bill mit gleichem Ø Investitionsgrad
  K5 (neu) Deflated Sharpe Ratio ≥ 0.95 nach N Versuchen laut Register (Zählweise aus der Anmeldung)
Sicht 2 (risiko-normiert, eine KENNZAHL, keine Empfehlung – es wird nichts gehebelt):
  Strategie rechnerisch auf die Schwankung von SPY im selben Zeitraum skaliert, Finanzierung zum T-Bill
  + Aufschlag. R1 Rendite p.a. höher als SPY · R2 max DD nicht schlimmer als SPY.
"""
from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass

from quantdesk.allocation import TRADING_DAYS, SimResult
from quantdesk.metrics import Perf, max_drawdown, perf
from quantdesk.multitest import Deflated, deflated_sharpe

SHARPE_MARGIN = 0.2
WINDOW_SHARE = 2 / 3
WINDOW_DAYS = 252
MIN_WINDOW_DAYS = 63
DSR_LEVEL = 0.95
FINANCING_SPREAD = 0.015  # Sicht 2: Kreditzins = T-Bill + 1.5 % (Annahme, ähnlich IBKR für kleine Konten)


def _ffill(series: list[tuple[str, float]], days: list[str], default: float) -> list[float]:
    keys = [d for d, _ in series]
    out = []
    for d in days:
        k = bisect.bisect_right(keys, d) - 1
        out.append(series[k][1] if k >= 0 else (series[0][1] if series else default))
    return out


@dataclass(frozen=True)
class Market:
    rates: list[tuple[str, float]]  # USD T-Bill p.a.
    fx: list[tuple[str, float]]  # CHF pro USD
    chf_rates: list[tuple[str, float]]  # CHF-Zins p.a.

    def rf(self, days: list[str], currency: str = "USD") -> list[float]:
        """Tageszins für den Schritt auf Tag i (Zins des Vortags / 252)."""
        annual = _ffill(self.rates if currency == "USD" else self.chf_rates, days, 0.0)
        return [0.0] + [a / TRADING_DAYS for a in annual[:-1]]


def in_currency(res: SimResult, market: Market, currency: str) -> tuple[list[float], list[float]]:
    if currency == "USD":
        return res.values, res.invested
    fx = _ffill(market.fx, res.days, 1.0)
    return [v * f for v, f in zip(res.values, fx)], [v * f for v, f in zip(res.invested, fx)]


def measure(res: SimResult, market: Market, currency: str = "USD") -> Perf:
    values, invested = in_currency(res, market, currency)
    return perf(values, invested, market.rf(res.days, currency))


def excess_returns(res: SimResult, market: Market, currency: str = "USD") -> list[float]:
    values, _ = in_currency(res, market, currency)
    rf = market.rf(res.days, currency)
    return [values[i] / values[i - 1] - 1 - rf[i] for i in range(1, len(values))]


def slice_result(res: SimResult, i0: int, i1: int) -> SimResult:
    return SimResult(res.name, res.days[i0:i1], res.values[i0:i1], res.invested[i0:i1])


def windows(n_days: int, size: int = WINDOW_DAYS, minimum: int = MIN_WINDOW_DAYS) -> list[tuple[int, int]]:
    """Testjahre als Index-Bereiche [i0, i1] (inkl. Starttag des Folgefensters als Anfangswert)."""
    out, i = [], 0
    while i < n_days - 1:
        j = min(i + size, n_days - 1)
        if j - i < minimum:
            break
        out.append((i, j + 1))
        i = j
    return out


def exposure_mix(spy: SimResult, exposure: float, market: Market) -> SimResult:
    """x % SPY + Rest Cash zum T-Bill, täglich auf x % zurückgesetzt."""
    rf = market.rf(spy.days)
    values = [spy.values[0]]
    for i in range(1, len(spy.values)):
        r = spy.values[i] / spy.values[i - 1] - 1
        values.append(values[-1] * (1 + exposure * r + (1 - exposure) * rf[i]))
    return SimResult(f"SPY {exposure:.0%} + Cash", spy.days, values, [v * exposure for v in values])


@dataclass(frozen=True)
class Verdict:
    currency: str
    perf: Perf
    spy: Perf
    mix: Perf
    windows_better: int
    windows_total: int
    deflated: Deflated
    k1: bool
    k2: bool
    k3: bool
    k4: bool
    k5: bool

    @property
    def passed(self) -> bool:
        return self.k1 and self.k2 and self.k3 and self.k4 and self.k5


def verdict(res: SimResult, spy: SimResult, market: Market, n_trials: int, currency: str = "USD") -> Verdict:
    if res.days != spy.days:
        raise ValueError("Strategie und SPY müssen denselben Zeitraum haben.")
    p, b = measure(res, market, currency), measure(spy, market, currency)
    wins = windows(len(res.days))
    better = sum(measure(slice_result(res, i0, i1), market, currency).sharpe >
                 measure(slice_result(spy, i0, i1), market, currency).sharpe for i0, i1 in wins)
    mix = measure(exposure_mix(spy, measure(res, market).avg_invested, market), market, currency)
    dsr = deflated_sharpe(excess_returns(res, market, currency), n_trials)
    return Verdict(currency, p, b, mix, better, len(wins), dsr,
                   k1=p.sharpe >= b.sharpe + SHARPE_MARGIN,
                   k2=p.max_drawdown >= b.max_drawdown,
                   k3=bool(wins) and better >= WINDOW_SHARE * len(wins),
                   k4=p.cagr > mix.cagr,
                   k5=dsr.passed(DSR_LEVEL))


# --------------------------------------------------------------------------- Sicht 2: risiko-normiert
@dataclass(frozen=True)
class Normalized:
    leverage: float  # nötiger Hebel auf SPY-Schwankung (< 1 = weniger investiert)
    cagr: float
    max_drawdown: float
    worst_month: float
    financing_cost: float  # Ø Finanzierungskosten p.a. (auf das Kapital)
    spy_cagr: float
    spy_max_drawdown: float
    spy_worst_month: float

    @property
    def r1(self) -> bool:
        return self.cagr > self.spy_cagr

    @property
    def r2(self) -> bool:
        return self.max_drawdown >= self.spy_max_drawdown

    @property
    def passed(self) -> bool:
        return self.r1 and self.r2


def _vol(values: list[float]) -> float:
    r = [b / a - 1 for a, b in zip(values, values[1:])]
    return statistics.pstdev(r) * math.sqrt(TRADING_DAYS)


def worst_month(days: list[str], values: list[float]) -> float:
    ends = [i for i in range(len(days)) if i == len(days) - 1 or days[i][:7] != days[i + 1][:7]]
    rets = [values[b] / values[a] - 1 for a, b in zip([0] + ends, ends) if b > a]
    return min(rets) if rets else 0.0


def normalized(res: SimResult, spy: SimResult, market: Market, spread: float = FINANCING_SPREAD) -> Normalized:
    """Strategie auf SPY-Schwankung skaliert: r_n = L·r − (L−1)·(T-Bill + Aufschlag) (bei L < 1: Rest zum T-Bill)."""
    lev = _vol(spy.values) / _vol(res.values)
    rf = market.rf(res.days)
    values, cost = [res.values[0]], []
    for i in range(1, len(res.values)):
        r = res.values[i] / res.values[i - 1] - 1
        fin = rf[i] + (spread / TRADING_DAYS if lev > 1 else 0.0)
        values.append(values[-1] * (1 + lev * r - (lev - 1) * fin))
        cost.append(max(lev - 1, 0) * fin)
    years = (len(values) - 1) / TRADING_DAYS
    cagr = (values[-1] / values[0]) ** (1 / years) - 1
    spy_cagr = (spy.values[-1] / spy.values[0]) ** (1 / years) - 1
    return Normalized(lev, cagr, max_drawdown(values), worst_month(res.days, values),
                      statistics.fmean(cost) * TRADING_DAYS if cost else 0.0,
                      spy_cagr, max_drawdown(spy.values), worst_month(spy.days, spy.values))


# --------------------------------------------------------------------------- Korrelation
def monthly_returns(res: SimResult) -> list[float]:
    ends = [i for i in range(len(res.days)) if i == len(res.days) - 1 or res.days[i][:7] != res.days[i + 1][:7]]
    return [res.values[b] / res.values[a] - 1 for a, b in zip([0] + ends, ends) if b > a]


def correlation(a: SimResult, b: SimResult) -> float:
    ra, rb = monthly_returns(a), monthly_returns(b)
    n = min(len(ra), len(rb))
    if n < 3 or statistics.pstdev(ra[:n]) == 0 or statistics.pstdev(rb[:n]) == 0:
        return float("nan")
    return statistics.correlation(ra[:n], rb[:n])
