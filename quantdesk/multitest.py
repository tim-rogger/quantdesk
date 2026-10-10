"""Mehrfachtest-Korrektur: Probabilistic und Deflated Sharpe Ratio (Bailey & López de Prado 2012/2014).

Problem: Wer N Strategien prüft und die beste nimmt, findet auch ohne echten Vorteil eine hohe Sharpe. Die DSR
fragt deshalb: Wie wahrscheinlich ist die wahre Sharpe > 0, wenn man berücksichtigt, dass die beste von N
Varianten gewählt wurde?

  PSR(SR*) = Φ( (SR − SR*) · √(T−1) / √(1 − γ3·SR + (γ4−1)/4 · SR²) )
  SR0      = √V · ( (1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)) )      γ = 0.5772 (Euler-Mascheroni)
  DSR      = PSR(SR0)

SR = Sharpe pro Periode (täglich, nicht annualisiert) der Überrendite über dem T-Bill, T = Anzahl Renditen,
γ3 = Schiefe, γ4 = Kurtosis (nicht Exzess). V = Varianz der Sharpe-Schätzer über die N Versuche. Ohne die
Renditen aller N Versuche nehmen wir die Varianz des Schätzers unter der Nullhypothese (wahre Sharpe 0):
V = 1/(T−1). Das ist der Erwartungswert der besten von N zufälligen Strategien ohne Vorteil
("False Strategy Theorem"). Weil sich viele Versuche ähneln, ist das eher streng.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

EULER_GAMMA = 0.5772156649015329
TRADING_DAYS = 252
_N = statistics.NormalDist()


def moments(returns: list[float]) -> tuple[float, float, float, float]:
    """(Mittel, Standardabweichung, Schiefe, Kurtosis) – Kurtosis nicht Exzess (Normalverteilung = 3)."""
    n = len(returns)
    if n < 3:
        raise ValueError("Mindestens 3 Renditen nötig.")
    mean = statistics.fmean(returns)
    dev = [r - mean for r in returns]
    m2 = sum(d * d for d in dev) / n
    if m2 <= 0:
        raise ValueError("Renditen ohne Schwankung.")
    m3 = sum(d ** 3 for d in dev) / n
    m4 = sum(d ** 4 for d in dev) / n
    return mean, math.sqrt(m2), m3 / m2 ** 1.5, m4 / m2 ** 2


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Erwartete höchste Sharpe (pro Periode) von N Versuchen ohne echten Vorteil."""
    if n_trials < 1:
        raise ValueError("N muss ≥ 1 sein.")
    if n_trials == 1:
        return 0.0
    z1 = _N.inv_cdf(1 - 1 / n_trials)
    z2 = _N.inv_cdf(1 - 1 / (n_trials * math.e))
    return math.sqrt(sr_variance) * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def psr(sr: float, sr_star: float, t: int, skew: float = 0.0, kurt: float = 3.0) -> float:
    """Probabilistic Sharpe Ratio: P(wahre Sharpe > sr_star). Alles pro Periode."""
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0 or t < 2:
        return float("nan")
    return _N.cdf((sr - sr_star) * math.sqrt(t - 1) / math.sqrt(denom))


@dataclass(frozen=True)
class Deflated:
    n_trials: int
    t: int
    sharpe: float  # annualisiert
    sharpe_threshold: float  # SR0 annualisiert: erwartete Bestleistung von N Zufallsstrategien
    skew: float
    kurtosis: float
    psr: float  # P(wahre Sharpe > 0) ohne Korrektur
    dsr: float  # P(wahre Sharpe > 0) nach Korrektur für N Versuche

    def passed(self, level: float = 0.95) -> bool:
        return self.dsr >= level


def deflated_sharpe(excess_returns: list[float], n_trials: int, periods: int = TRADING_DAYS) -> Deflated:
    """DSR für tägliche Überrenditen (Rendite − T-Bill) nach N Versuchen."""
    mean, sd, skew, kurt = moments(excess_returns)
    t = len(excess_returns)
    sr = mean / sd
    sr0 = expected_max_sharpe(n_trials, 1 / (t - 1))
    ann = math.sqrt(periods)
    return Deflated(n_trials, t, sr * ann, sr0 * ann, skew, kurt, psr(sr, 0.0, t, skew, kurt),
                    psr(sr, sr0, t, skew, kurt))


def required_sharpe(n_trials: int, years: float, level: float = 0.95, periods: int = TRADING_DAYS,
                    skew: float = 0.0, kurt: float = 3.0) -> float:
    """Annualisierte Sharpe, die nach N Versuchen und `years` Jahren Testdauer für DSR ≥ level nötig ist."""
    t = int(round(years * periods))
    sr0 = expected_max_sharpe(n_trials, 1 / (t - 1))
    lo, hi = 0.0, 1.0  # pro Periode
    for _ in range(100):
        mid = (lo + hi) / 2
        if psr(mid, sr0, t, skew, kurt) >= level:
            hi = mid
        else:
            lo = mid
    return hi * math.sqrt(periods)
