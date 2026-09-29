"""Walk-forward-Test und Bestehen-Regel (siehe ROADMAP.md).

Ablauf: 3 Jahre Training, 1 Jahr Test, dann um 1 Jahr verschieben. Jede Strategie startet in jedem
Testjahr neu. Die Testjahre werden zu einer Kapitalkurve aneinandergehängt. Nicht investiertes Geld
bekommt den 3-Monats-T-Bill-Zins, die Sharpe misst die Überrendite über diesem Zins. Ausgewertet wird
in USD und in CHF (Kurs USD/CHF, CHF-Zins vereinfacht = 0). Handelskosten sind in allen Strategien drin.

Bestehen-Regel – alle vier müssen gelten (Massstab: Kaufen & Halten SPY):
  K1  Sharpe über alle Testjahre mindestens SHARPE_MARGIN (0.2) höher als SPY
  K2  max Drawdown über alle Testjahre nicht schlimmer als bei SPY
  K3  in mindestens 2/3 der einzelnen Testjahre höhere Sharpe als SPY
  K4  mehr Rendite p.a. als eine Mischung aus SPY + Cash mit demselben Ø Investitionsgrad
      (sonst gewinnt die Strategie nur, weil sie viel Cash hält)
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Callable

from quantdesk.backtest import Costs, GridParams
from quantdesk.history import Bar
from quantdesk.metrics import TRADING_DAYS, Perf, add_cash_interest, perf
from quantdesk.research import (
    GRID_VARIANTS,
    WARMUP,
    Period,
    Row,
    _period_days,
    best_grid_on,
    calendar,
    dual_momentum,
    grid_portfolio,
    hold_single,
    mix_row,
    momentum_portfolio,
)

TRAIN_DAYS = 3 * 252
TEST_DAYS = 252
MIN_TEST_DAYS = 63  # ein angebrochenes letztes Testfenster zählt nur, wenn es mind. ~3 Monate hat
WINDOW_SHARE = 2 / 3
SHARPE_MARGIN = 0.2
CURRENCIES = ("USD", "CHF")

# ETFs für Kandidat A (Dual Momentum) und die Vergleichsportfolios
ETF_SYMBOLS = ("SPY", "EFA", "EEM", "TLT", "GLD", "AGG", "BIL", "VT")
RATE_SYMBOL = "^IRX"  # 13-Wochen-T-Bill-Rendite in %
FX_SYMBOL = "CHF=X"  # CHF pro USD


@dataclass(frozen=True)
class Window:
    train: Period
    test: Period


def windows(cal: list[str], warmup: int = WARMUP, train: int = TRAIN_DAYS, test: int = TEST_DAYS) -> list[Window]:
    out, i = [], warmup
    while i + train < len(cal):
        t0, t1 = i + train, min(i + train + test, len(cal))
        if t1 - t0 < MIN_TEST_DAYS:
            break
        out.append(Window(Period("TRAIN", cal[i], cal[t0 - 1]), Period("TEST", cal[t0], cal[t1 - 1])))
        i += test
    return out


# --------------------------------------------------------------------------- Markt: Zins & Währung
@dataclass(frozen=True)
class Market:
    rates: tuple[list[str], list[float]] = ((), ())  # (Tage, T-Bill-Jahreszins als Dezimalzahl)
    fx: tuple[list[str], list[float]] = ((), ())  # (Tage, CHF pro USD)

    @staticmethod
    def _ffill(series: tuple[list[str], list[float]], days: list[str], default: float) -> list[float]:
        keys, vals = series
        out = []
        for d in days:
            k = bisect.bisect_right(keys, d) - 1
            out.append(vals[k] if k >= 0 else (vals[0] if vals else default))
        return out

    def daily_rates(self, days: list[str]) -> list[float]:
        """Tageszins für den Schritt auf Tag i (Zins des Vortags / 252)."""
        annual = self._ffill(self.rates, days, 0.0)
        return [0.0] + [a / TRADING_DAYS for a in annual[:-1]]

    def fx_rates(self, days: list[str]) -> list[float]:
        return self._ffill(self.fx, days, 1.0)

    @classmethod
    def from_bars(cls, rate_bars: list[Bar] | None, fx_bars: list[Bar] | None) -> "Market":
        rates = ([b.day for b in rate_bars], [b.close / 100 for b in rate_bars]) if rate_bars else ((), ())
        fx = ([b.day for b in fx_bars], [b.close for b in fx_bars]) if fx_bars else ((), ())
        return cls(rates, fx)


@dataclass(frozen=True)
class Curve:
    days: list[str]
    values: list[float]
    invested: list[float]


def window_curve(row: Row, period: Period, cal: list[str]) -> Curve:
    days = _period_days(cal, period)
    inv = list(row.invested) or list(row.values)
    return Curve(days, list(row.values), inv)


def stitch(curves: list[Curve]) -> Curve:
    """Kapitalkurven der Testfenster aneinanderhängen (jedes Fenster startet dort, wo das letzte endete)."""
    days: list[str] = []
    values: list[float] = []
    invested: list[float] = []
    for c in curves:
        scale = values[-1] / c.values[0] if values else 1.0
        skip = 1 if values else 0
        days += c.days[skip:]
        values += [x * scale for x in c.values[skip:]]
        invested += [x * scale for x in c.invested[skip:]]
    return Curve(days, values, invested)


def measure(curve: Curve, market: Market, currency: str = "USD") -> Perf:
    """Cash verzinsen (USD-T-Bill), dann in der gewünschten Währung auswerten."""
    rates = market.daily_rates(curve.days)
    values = add_cash_interest(curve.values, curve.invested, rates)
    invested = curve.invested
    rf = rates
    if currency == "CHF":
        fx = market.fx_rates(curve.days)
        values = [v * f for v, f in zip(values, fx)]
        invested = [v * f for v, f in zip(invested, fx)]
        rf = None  # CHF-Zins 2019–2026 nahe 0 – vereinfacht
    return perf(values, invested, rf)


def exposure_mix(spy: Curve, exposure: float) -> Curve:
    """x % SPY + Rest Cash, täglich auf x % zurückgesetzt (Zins aufs Cash kommt in measure dazu)."""
    values, invested = [spy.values[0]], [spy.values[0] * exposure]
    for a, b in zip(spy.values, spy.values[1:]):
        values.append(values[-1] * (1 + exposure * (b / a - 1)))
        invested.append(values[-1] * exposure)
    return Curve(spy.days, values, invested)


# --------------------------------------------------------------------------- Ergebnis & Urteil
@dataclass(frozen=True)
class WFResult:
    name: str
    windows: list[Curve]
    stitched: Curve
    notes: list[str]  # z.B. gewählte Parameter pro Fenster


@dataclass(frozen=True)
class Verdict:
    currency: str
    perf: Perf
    window_sharpes: list[float]
    mix: Perf  # SPY + Cash mit gleichem Investitionsgrad
    k1: bool
    k2: bool
    k3: bool
    k4: bool
    windows_better: int

    @property
    def passed(self) -> bool:
        return self.k1 and self.k2 and self.k3 and self.k4


def check(candidate: WFResult, spy: WFResult, market: Market, currency: str = "USD") -> Verdict:
    p = measure(candidate.stitched, market, currency)
    b = measure(spy.stitched, market, currency)
    ws = [measure(c, market, currency).sharpe for c in candidate.windows]
    bs = [measure(c, market, currency).sharpe for c in spy.windows]
    better = sum(x > y for x, y in zip(ws, bs))
    mix = measure(exposure_mix(spy.stitched, measure(candidate.stitched, market, "USD").avg_invested), market, currency)
    return Verdict(
        currency=currency,
        perf=p,
        window_sharpes=ws,
        mix=mix,
        k1=p.sharpe >= b.sharpe + SHARPE_MARGIN,
        k2=p.max_drawdown >= b.max_drawdown,
        k3=bool(bs) and better >= WINDOW_SHARE * len(bs),
        k4=p.cagr > mix.cagr,
        windows_better=better,
    )


# --------------------------------------------------------------------------- Strategien
# Eine Strategie im Walk-forward: (Aktien, Fenster, Kalender, Kosten) -> (Row fürs Testfenster, Notiz)
StrategyFn = Callable[[dict[str, list[Bar]], Window, list[str], Costs], tuple[Row, str]]


def _fixed_grid(params: GridParams) -> StrategyFn:
    return lambda data, w, cal, costs: (grid_portfolio(data, params, costs, w.test, cal)[0], "")


def _equal_weight(data, w, cal, costs):
    return grid_portfolio(data, GRID_VARIANTS[0], costs, w.test, cal)[1], ""


def _momentum(data, w, cal, costs):
    return momentum_portfolio(data, w.test, cal, costs), ""


def _tuned_grid(grid: list[GridParams] | None, progress=None) -> StrategyFn:
    def run(data, w, cal, costs):
        best = best_grid_on(data, costs, w.train, cal, grid, progress)
        return grid_portfolio(data, best, costs, w.test, cal)[0], best.label()
    return run


DM_CLASSIC = ("Dual Momentum klassisch: SPY/EFA, sonst AGG", ("SPY", "EFA"))
DM_BROAD = ("Dual Momentum breit: SPY/EFA/EEM/TLT/GLD, sonst AGG", ("SPY", "EFA", "EEM", "TLT", "GLD"))


def _dual(etfs: dict[str, list[Bar]], name: str, risky: tuple[str, ...]) -> StrategyFn:
    return lambda data, w, cal, costs: (dual_momentum(etfs, risky, "AGG", "BIL", w.test, cal, name, costs), "")


def default_strategies(
    tune: bool = True,
    grid: list[GridParams] | None = None,
    progress=None,
    etfs: dict[str, list[Bar]] | None = None,
) -> dict[str, StrategyFn]:
    s: dict[str, StrategyFn] = {"Kaufen & Halten, alle gleich gewichtet": _equal_weight}
    for p in GRID_VARIANTS:
        s[f"Grid {p.label()}"] = _fixed_grid(p)
    if tune:
        s["Grid, je Trainingsfenster optimiert"] = _tuned_grid(grid, progress)
    s["Momentum Top 5, 63 Tage, monatlich"] = _momentum
    if etfs:
        for name, risky in (DM_CLASSIC, DM_BROAD):
            s[name] = _dual(etfs, name, risky)
    return s


def benchmark_fns(benchmark: list[Bar], benchmark_name: str, etfs: dict[str, list[Bar]] | None) -> dict[str, StrategyFn]:
    """Vergleichsportfolios (keine Kandidaten): SPY (Massstab), Welt-ETF VT, 60/40 SPY/AGG."""
    out: dict[str, StrategyFn] = {
        f"Kaufen & Halten {benchmark_name} (ETF)":
            lambda d, w, cal, c: (hold_single(benchmark, w.test, cal, benchmark_name), ""),
    }
    if etfs and "VT" in etfs:
        out["Kaufen & Halten VT (Welt-ETF)"] = lambda d, w, cal, c: (hold_single(etfs["VT"], w.test, cal, "VT"), "")
    if etfs and {"SPY", "AGG"} <= set(etfs):
        out["60/40 SPY/AGG, monatlich"] = lambda d, w, cal, c: (
            mix_row(etfs, {"SPY": 0.6, "AGG": 0.4}, w.test, cal, "60/40", c), "")
    return out


def walk_forward(
    data: dict[str, list[Bar]],
    benchmark: list[Bar],
    benchmark_name: str = "SPY",
    costs: Costs = Costs(),
    strategies: dict[str, StrategyFn] | None = None,
    etfs: dict[str, list[Bar]] | None = None,
) -> tuple[list[Window], list[WFResult], list[WFResult]]:
    """Liefert (Fenster, Vergleichsportfolios [SPY zuerst], Strategien). Urteil danach mit check()."""
    cal = calendar(list(data.values()) + [benchmark])
    wins = windows(cal)
    if not wins:
        raise ValueError("Zu wenige Daten für den Walk-forward-Test (mind. 1 Jahr Vorlauf + 3 Jahre + 3 Monate).")
    strategies = strategies if strategies is not None else default_strategies(etfs=etfs)

    def run(name: str, fn: StrategyFn) -> WFResult:
        curves, notes = [], []
        for w in wins:
            row, note = fn(data, w, cal, costs)
            curves.append(window_curve(row, w.test, cal))
            notes.append(note)
        return WFResult(name, curves, stitch(curves), notes)

    benches = [run(n, fn) for n, fn in benchmark_fns(benchmark, benchmark_name, etfs).items()]
    return wins, benches, [run(n, fn) for n, fn in strategies.items()]
