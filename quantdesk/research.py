"""Strategie-Vergleich auf Portfolio-Ebene: Grid/DCA (mit/ohne Trendfilter), Momentum-Rotation,
Kaufen und Halten (gleichgewichtet) und ein breiter ETF – mit Trainings- und Testzeitraum.

Alle Strategien laufen auf demselben Kalender, derselben Kostenannahme und werden mit denselben
Kennzahlen (quantdesk.metrics.perf) bewertet. Das erste Jahr dient nur als Vorlauf (Trend-SMA,
Momentum-Lookback), gehandelt wird danach.
"""
from __future__ import annotations

import bisect
import csv
from dataclasses import dataclass

from quantdesk.backtest import Costs, GridParams, default_grid, simulate
from quantdesk.history import Bar
from quantdesk.metrics import Perf, perf

WARMUP = 252  # Handelstage Vorlauf vor dem ersten Handelstag


@dataclass(frozen=True)
class Row:
    name: str
    perf: Perf
    trades: int
    symbols: int


@dataclass(frozen=True)
class Period:
    name: str
    start: str  # erster Handelstag (inkl.)
    end: str  # letzter Handelstag (inkl.)


def load_universe(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        lines = [line for line in f if line.strip() and not line.startswith("#")]
    return [row["symbol"].strip().upper() for row in csv.DictReader(lines)]


def calendar(series: list[list[Bar]]) -> list[str]:
    return sorted({b.day for bars in series for b in bars})


def periods(cal: list[str], warmup: int = WARMUP) -> list[Period]:
    """Training = 1. Hälfte nach dem Vorlauf, Test = 2. Hälfte, dazu der Gesamtzeitraum."""
    if len(cal) < warmup + 20:
        raise ValueError("Zu wenige Handelstage für Vorlauf + Backtest.")
    start = warmup
    cut = start + (len(cal) - start) // 2
    return [
        Period("TRAINING", cal[start], cal[cut - 1]),
        Period("TEST", cal[cut], cal[-1]),
        Period("GESAMT", cal[start], cal[-1]),
    ]


def trading_range(bars: list[Bar], period: Period, warmup: int = WARMUP) -> tuple[int, int] | None:
    """Index-Bereich [i0, i1) dieses Symbols im Zeitraum – erst nach eigenem Vorlauf handelbar."""
    days = [b.day for b in bars]
    i0 = max(bisect.bisect_left(days, period.start), warmup)
    i1 = bisect.bisect_right(days, period.end)
    return (i0, i1) if i1 - i0 >= 20 else None


def _align(days: list[str], values: list[float], cal: list[str], before: float) -> list[float]:
    """Werte auf den Kalender legen: vor dem Start `before`, Lücken mit dem letzten Wert füllen."""
    out, j, last = [], 0, before
    for day in cal:
        while j < len(days) and days[j] <= day:
            last = values[j]
            j += 1
        out.append(last)
    return out


def _period_days(cal: list[str], period: Period) -> list[str]:
    return cal[bisect.bisect_left(cal, period.start): bisect.bisect_right(cal, period.end)]


def grid_portfolio(data: dict[str, list[Bar]], params: GridParams, costs: Costs, period: Period, cal: list[str]):
    """Grid pro Symbol mit eigenem Budget, alle Konten summiert. Liefert (Row, Kaufen-und-Halten-Row)."""
    days = _period_days(cal, period)
    budget = (params.levels + 1) * costs.order_usd
    acc = [0.0] * len(days)
    inv = [0.0] * len(days)
    hold = [0.0] * len(days)
    trades = n = 0
    for sym, bars in data.items():
        rng = trading_range(bars, period)
        if rng is None:
            continue
        r = simulate(bars, params, costs, sym, *rng)
        n += 1
        trades += r.buys + r.sells
        for total, series, before in ((acc, r.account, budget), (inv, r.invested, 0.0), (hold, r.hold_account, budget)):
            for k, v in enumerate(_align(r.days, series, days, before)):
                total[k] += v
    if n == 0:
        raise ValueError("Kein Symbol mit genug Daten im Zeitraum.")
    return (
        Row(f"Grid {params.label()}", perf(acc, inv), trades, n),
        Row("Kaufen & Halten, alle gleich gewichtet", perf(hold, hold), 2 * n, n),
    )


def hold_single(bars: list[Bar], period: Period, cal: list[str], name: str, capital: float = 100_000.0) -> Row:
    days = _period_days(cal, period)
    i0 = bisect.bisect_left([b.day for b in bars], period.start)
    i1 = bisect.bisect_right([b.day for b in bars], period.end)
    part = bars[i0:i1]
    shares = capital / part[0].open
    values = _align([b.day for b in part], [shares * b.close for b in part], days, capital)
    return Row(name, perf(values, values), 2, 1)


def momentum_portfolio(
    data: dict[str, list[Bar]],
    period: Period,
    cal: list[str],
    costs: Costs = Costs(),
    top_n: int = 5,
    lookback: int = 63,
    capital: float = 100_000.0,
) -> Row:
    """Momentum-Rotation wie im alten Java-QuantDesk, aber ohne Blick in die Zukunft:

    Am ersten Handelstag jedes Monats: Rendite der letzten `lookback` Tage bis zum VORTAG berechnen,
    die `top_n` besten mit positiver Rendite halten. Aussteiger zum Eröffnungskurs verkaufen, freies Geld
    gleichmässig auf die Einsteiger verteilen (bestehende Positionen werden nicht umgeschichtet).
    Keine Aktie mit positivem Momentum -> Cash.
    """
    days = _period_days(cal, period)
    index = {sym: {b.day: i for i, b in enumerate(bars)} for sym, bars in data.items()}
    cash = capital
    holdings: dict[str, float] = {}
    last_close: dict[str, float] = {}
    values, invested = [], []
    trades, month = 0, None
    for day in days:
        if day[:7] != month:
            month = day[:7]
            scores = []
            for sym, bars in data.items():
                j = index[sym].get(day)
                if j is None or j - 1 - lookback < 0:
                    continue
                mom = bars[j - 1].close / bars[j - 1 - lookback].close - 1
                if mom > 0:
                    scores.append((-mom, sym))
            target = [sym for _, sym in sorted(scores)[:top_n]]
            for sym in list(holdings):
                j = index[sym].get(day)
                if sym not in target and j is not None:
                    cash += holdings.pop(sym) * data[sym][j].open - costs.fee
                    trades += 1
            entrants = [s for s in target if s not in holdings]
            if entrants and cash > 0:
                per = cash / len(entrants)
                for sym in entrants:
                    price = data[sym][index[sym][day]].open
                    holdings[sym] = (per - costs.fee) / price
                    cash -= per
                    trades += 1
        for sym in holdings:
            j = index[sym].get(day)
            if j is not None:
                last_close[sym] = data[sym][j].close
        pos = sum(q * last_close[sym] for sym, q in holdings.items())
        values.append(cash + pos)
        invested.append(pos)
    return Row(f"Momentum Top {top_n}, {lookback} Tage, monatlich", perf(values, invested), trades, len(data))


GRID_VARIANTS = (
    GridParams(5, 0.02),  # Video
    GridParams(5, 0.02, trend_sma=200),
    GridParams(5, 0.02, trend_sma=200, trend_exit=True),
)


def best_grid_on(data, costs: Costs, period: Period, cal: list[str], grid: list[GridParams] | None = None, progress=None):
    """Grid-Parameter mit der höchsten Portfolio-Sharpe im (Trainings-)Zeitraum."""
    grid = grid or default_grid()
    best, best_sharpe = grid[0], float("-inf")
    for n, params in enumerate(grid, 1):
        sharpe = grid_portfolio(data, params, costs, period, cal)[0].perf.sharpe
        if sharpe > best_sharpe:
            best, best_sharpe = params, sharpe
        if progress:
            progress(n, len(grid))
    return best


def study(
    data: dict[str, list[Bar]],
    benchmark: list[Bar],
    benchmark_name: str = "SPY",
    costs: Costs = Costs(),
    tuned_grid: GridParams | None = None,
) -> dict[str, list[Row]]:
    """Tabelle pro Zeitraum. `tuned_grid` = im Training gewählte Parameter (wird in allen Zeiträumen gezeigt)."""
    cal = calendar(list(data.values()) + [benchmark])
    out = {}
    for period in periods(cal):
        rows = [hold_single(benchmark, period, cal, f"Kaufen & Halten {benchmark_name} (ETF)")]
        grid_rows = [grid_portfolio(data, p, costs, period, cal) for p in GRID_VARIANTS]
        rows.append(grid_rows[0][1])  # gleichgewichtetes Kaufen & Halten
        rows += [g for g, _ in grid_rows]
        if tuned_grid is not None:
            g = grid_portfolio(data, tuned_grid, costs, period, cal)[0]
            rows.append(Row(g.name + " [im Training optimiert]", g.perf, g.trades, g.symbols))
        rows.append(momentum_portfolio(data, period, cal, costs))
        out[f"{period.name} {period.start} – {period.end}"] = rows
    return out
