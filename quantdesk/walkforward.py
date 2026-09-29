"""Walk-forward-Test und Bestehen-Regel (siehe ROADMAP.md).

Ablauf: 3 Jahre Training, 1 Jahr Test, dann um 1 Jahr verschieben. Jede Strategie startet in jedem
Testjahr neu (Cash). Die Testjahre werden zu einer Kapitalkurve aneinandergehängt und mit Kaufen & Halten
des ETFs über dieselben Jahre verglichen. Nur Strategien mit Parametern (Grid optimiert) nutzen das
Trainingsfenster – feste Regeln ignorieren es.

Bestehen-Regel – alle drei müssen gelten:
  K1  Sharpe über alle Testjahre höher als beim ETF
  K2  max Drawdown über alle Testjahre nicht schlimmer als beim ETF
  K3  in mindestens 2/3 der einzelnen Testjahre höhere Sharpe als der ETF
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from quantdesk.backtest import Costs, GridParams
from quantdesk.history import Bar
from quantdesk.metrics import Perf, perf
from quantdesk.research import (
    GRID_VARIANTS,
    WARMUP,
    Period,
    Row,
    best_grid_on,
    calendar,
    grid_portfolio,
    hold_single,
    momentum_portfolio,
)

TRAIN_DAYS = 3 * 252
TEST_DAYS = 252
MIN_TEST_DAYS = 63  # ein angebrochenes letztes Testfenster zählt nur, wenn es mind. ~3 Monate hat
WINDOW_SHARE = 2 / 3


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


def stitch(rows: list[Row]) -> tuple[list[float], list[float]]:
    """Kapitalkurven der Testfenster aneinanderhängen (jedes Fenster startet dort, wo das letzte endete)."""
    values: list[float] = []
    invested: list[float] = []
    for row in rows:
        v, inv = list(row.values), list(row.invested) or list(row.values)
        scale = values[-1] / v[0] if values else 1.0
        skip = 1 if values else 0
        values += [x * scale for x in v[skip:]]
        invested += [x * scale for x in inv[skip:]]
    return values, invested


@dataclass(frozen=True)
class WFResult:
    name: str
    windows: list[Row]
    stitched: Perf
    notes: list[str]  # z.B. gewählte Parameter pro Fenster


@dataclass(frozen=True)
class Verdict:
    k1: bool
    k2: bool
    k3: bool
    windows_better: int
    windows_total: int

    @property
    def passed(self) -> bool:
        return self.k1 and self.k2 and self.k3


def check(candidate: WFResult, benchmark: WFResult) -> Verdict:
    better = sum(c.perf.sharpe > b.perf.sharpe for c, b in zip(candidate.windows, benchmark.windows))
    total = len(benchmark.windows)
    return Verdict(
        k1=candidate.stitched.sharpe > benchmark.stitched.sharpe,
        k2=candidate.stitched.max_drawdown >= benchmark.stitched.max_drawdown,
        k3=total > 0 and better >= WINDOW_SHARE * total,
        windows_better=better,
        windows_total=total,
    )


# Eine Strategie im Walk-forward: (Daten, Fenster, Kalender, Kosten) -> (Row fürs Testfenster, Notiz)
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


def default_strategies(tune: bool = True, grid: list[GridParams] | None = None, progress=None) -> dict[str, StrategyFn]:
    s: dict[str, StrategyFn] = {"Kaufen & Halten, alle gleich gewichtet": _equal_weight}
    for p in GRID_VARIANTS:
        s[f"Grid {p.label()}"] = _fixed_grid(p)
    if tune:
        s["Grid, je Trainingsfenster optimiert"] = _tuned_grid(grid, progress)
    s["Momentum Top 5, 63 Tage, monatlich"] = _momentum
    return s


def walk_forward(
    data: dict[str, list[Bar]],
    benchmark: list[Bar],
    benchmark_name: str = "SPY",
    costs: Costs = Costs(),
    strategies: dict[str, StrategyFn] | None = None,
) -> tuple[list[Window], WFResult, list[tuple[WFResult, Verdict]]]:
    """Liefert (Fenster, ETF-Ergebnis, [(Strategie-Ergebnis, Urteil)])."""
    cal = calendar(list(data.values()) + [benchmark])
    wins = windows(cal)
    if not wins:
        raise ValueError("Zu wenige Daten für den Walk-forward-Test (mind. 1 Jahr Vorlauf + 3 Jahre + 3 Monate).")
    strategies = strategies if strategies is not None else default_strategies()

    def result(name: str, rows: list[Row], notes: list[str]) -> WFResult:
        return WFResult(name, rows, perf(*stitch(rows)), notes)

    bench_rows = [hold_single(benchmark, w.test, cal, benchmark_name) for w in wins]
    bench = result(f"Kaufen & Halten {benchmark_name} (ETF)", bench_rows, [""] * len(wins))
    out = []
    for name, fn in strategies.items():
        rows, notes = [], []
        for w in wins:
            row, note = fn(data, w, cal, costs)
            rows.append(row)
            notes.append(note)
        res = result(name, rows, notes)
        out.append((res, check(res, bench)))
    return wins, bench, out
