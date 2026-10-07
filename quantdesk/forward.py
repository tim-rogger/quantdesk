"""Vorwärtstest von Kandidat C (Grid 5×2 %, SMA200 + Exit) auf dem Paper-Konto – Einrichtung und Report.

Die Bestehen-Kriterien stehen in ROADMAP.md und wurden VOR dem Start festgelegt:
  F1  Rendite C > Mischung SPY + Cash mit gleichem Ø Investitionsgrad (USD und CHF)
  F2  max Drawdown C nicht schlimmer als SPY im selben Zeitraum (USD und CHF)
  F3  in mind. 4 von 6 Testmonaten (je 21 Handelstage) Monatsrendite C ≥ Mischung (USD)
  F4  Gesamtrendite live weicht höchstens 2 Prozentpunkte vom Backtest desselben Zeitraums ab
  F5  keine technischen Fehler: keine doppelt gebuchten Orders, kein zweiter Einstieg ohne Verkauf dazwischen
Der Test dauert 6 Testmonate = 126 Handelstage ab dem ersten echten Paper-Fill.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass

from quantdesk import storage
from quantdesk.backtest import Costs, GridParams
from quantdesk.history import Bar
from quantdesk.journal import Fill
from quantdesk.research import Period, calendar, grid_portfolio
from quantdesk.strategy import EquitySystem
from quantdesk.walkforward import Curve, Market, exposure_mix, measure

C_PARAMS = GridParams(5, 0.02, trend_sma=200, trend_exit=True)
DEFAULT_ORDER_USD = 1000.0
MONTH_DAYS = 21
TEST_MONTHS = 6
MIN_MONTHS_BETTER = 4
MAX_BACKTEST_GAP = 0.02


# --------------------------------------------------------------------------- Einrichtung
def setup_systems(data_file: str, symbols: list[str], order_usd: float = DEFAULT_ORDER_USD) -> tuple[list[str], list[str]]:
    """C-Systeme (Status Off) in equities.json anlegen. Bestehende Symbole werden nicht verändert."""
    systems = storage.load(data_file)
    added, skipped = [], []
    for sym in symbols:
        if sym in systems:
            skipped.append(sym)
            continue
        systems[sym] = EquitySystem(sym, C_PARAMS.levels, C_PARAMS.drawdown, trend_sma=C_PARAMS.trend_sma,
                                    trend_exit=C_PARAMS.trend_exit, order_usd=order_usd)
        added.append(sym)
    storage.save(data_file, systems)
    return added, skipped


def c_systems(data_file: str) -> list[EquitySystem]:
    return [s for s in storage.load(data_file).values()
            if s.trend_sma == C_PARAMS.trend_sma and s.trend_exit and s.order_usd]


# --------------------------------------------------------------------------- Kapitalkurven
def _closes(bars: list[Bar]) -> tuple[list[str], list[float]]:
    return [b.day for b in bars], [b.close for b in bars]


def _close_on(series: tuple[list[str], list[float]], day: str) -> float | None:
    days, closes = series
    k = bisect.bisect_right(days, day) - 1
    return closes[k] if k >= 0 else None


def live_curve(fills: list[Fill], bars: dict[str, list[Bar]], days: list[str], budget: float, fee: float) -> Curve:
    """Kontowert von C pro Tag aus dem Journal: Budget - Käufe + Verkäufe - Gebühren + Positionen zum Schlusskurs."""
    by_day: dict[str, list[Fill]] = defaultdict(list)
    for f in fills:
        by_day[f.day].append(f)
    series = {s: _closes(b) for s, b in bars.items()}
    qty: dict[str, float] = defaultdict(float)
    cash = budget
    pending = sorted(d for d in by_day if d < days[0])  # Fills vor dem ersten Kalendertag
    for d in pending:
        for f in by_day[d]:
            cash, qty = _apply(f, cash, qty, fee)
    values, invested = [], []
    for day in days:
        for f in by_day.get(day, []):
            cash, qty = _apply(f, cash, qty, fee)
        pos = sum(q * (_close_on(series[s], day) or 0.0) for s, q in qty.items() if q and s in series)
        values.append(cash + pos)
        invested.append(pos)
    return Curve(days, values, invested)


def _apply(f: Fill, cash: float, qty: dict[str, float], fee: float):
    sign = 1 if f.side == "BUY" else -1
    qty[f.symbol] += sign * f.qty
    cash -= sign * f.qty * f.price + fee
    return cash, qty


def backtest_curve(bars: dict[str, list[Bar]], start: str, end: str, cal: list[str], order_usd: float, fee: float) -> Curve:
    period = Period("FORWARD", start, end)
    row = grid_portfolio(bars, C_PARAMS, Costs(order_usd, fee), period, cal)[0]
    days = cal[bisect.bisect_left(cal, start): bisect.bisect_right(cal, end)]
    return Curve(days, list(row.values), list(row.invested))


def hold_curve(bars: list[Bar], days: list[str], capital: float) -> Curve:
    series = _closes(bars)
    first = next(b for b in bars if b.day >= days[0])
    shares = capital / first.open
    values = [shares * (_close_on(series, d) or first.open) for d in days]
    return Curve(days, values, values)


def monthly_returns(curve: Curve, month_days: int = MONTH_DAYS) -> list[float]:
    """Rendite je Block von `month_days` Handelstagen ab Start (letzter Block kann kürzer sein)."""
    out, v = [], curve.values
    for i in range(0, len(v) - 1, month_days):
        j = min(i + month_days, len(v) - 1)
        out.append(v[j] / v[i] - 1)
    return out


# --------------------------------------------------------------------------- Report
@dataclass(frozen=True)
class Check:
    code: str
    text: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ForwardReport:
    start: str
    end: str
    trading_days: int
    months_done: int
    finished: bool
    rows: dict[str, dict[str, object]]  # Name -> {"USD": Perf, "CHF": Perf}
    monthly: dict[str, list[float]]  # Name -> Monatsrenditen (USD)
    checks: list[Check]
    estimated_fills: int = 0  # Fills mit geschätztem Tag (Ausführung bei IBKR nicht mehr abrufbar)

    @property
    def passed(self) -> bool:
        return self.finished and all(c.ok for c in self.checks)


def technical_problems(fills: list[Fill]) -> list[str]:
    problems = []
    seen = set()
    for f in fills:
        key = (f.order_id, f.side, f.kind)
        if key in seen and f.order_id not in ("bestand", ""):
            problems.append(f"Order {f.order_id} ({f.symbol}) doppelt im Journal")
        seen.add(key)
    open_cycle: dict[str, bool] = {}
    for f in fills:
        if f.kind in ("entry", "adopted"):
            if open_cycle.get(f.symbol):
                problems.append(f"{f.symbol}: zweiter Einstieg am {f.day} ohne Verkauf dazwischen")
            open_cycle[f.symbol] = True
        elif f.kind == "exit":
            open_cycle[f.symbol] = False
    return problems


def build_report(
    fills: list[Fill],
    bars: dict[str, list[Bar]],
    spy: list[Bar],
    market: Market,
    budget: float,
    order_usd: float = DEFAULT_ORDER_USD,
    fee: float = 1.0,
    start: str | None = None,
) -> ForwardReport:
    fills = sorted(fills, key=lambda f: f.ts)
    if start is None:
        if not fills:
            raise ValueError("Noch keine Fills im Journal – der Vorwärtstest hat nicht begonnen.")
        start = fills[0].day
    cal = [d for d in calendar(list(bars.values()) + [spy]) if d >= start]
    if len(cal) < 2:
        raise ValueError(f"Der Vorwärtstest läuft erst seit {start} – ein Report ist ab dem 2. Handelstag möglich.")
    days = cal
    end = days[-1]
    full_cal = calendar(list(bars.values()) + [spy])
    live = live_curve([f for f in fills if f.day >= start], bars, days, budget, fee)
    bt = backtest_curve(bars, start, end, full_cal, order_usd, fee)
    spy_c = hold_curve(spy, days, budget)
    exposure = measure(live, market).avg_invested
    mix = exposure_mix(spy_c, exposure)
    curves = {"C live (Paper)": live, "C Backtest (gleicher Zeitraum)": bt, "SPY halten": spy_c,
              f"Mischung {exposure:.0%} SPY + Cash": mix}
    rows = {name: {cur: measure(c, market, cur) for cur in ("USD", "CHF")} for name, c in curves.items()}
    monthly = {name: monthly_returns(c) for name, c in curves.items()}
    months_done = (len(days) - 1) // MONTH_DAYS
    finished = len(days) - 1 >= TEST_MONTHS * MONTH_DAYS

    mix_name = f"Mischung {exposure:.0%} SPY + Cash"
    lv, sp, mx, b = rows["C live (Paper)"], rows["SPY halten"], rows[mix_name], rows["C Backtest (gleicher Zeitraum)"]
    better_months = sum(x >= y for x, y in zip(monthly["C live (Paper)"], monthly[mix_name]))
    n_months = len(monthly["C live (Paper)"])
    gap = lv["USD"].total_return - b["USD"].total_return
    problems = technical_problems(fills)
    checks = [
        Check("F1", "Rendite C > Mischung SPY + Cash (USD und CHF)",
              all(lv[c].total_return > mx[c].total_return for c in ("USD", "CHF")),
              f"USD {lv['USD'].total_return:+.1%} vs {mx['USD'].total_return:+.1%}, "
              f"CHF {lv['CHF'].total_return:+.1%} vs {mx['CHF'].total_return:+.1%}"),
        Check("F2", "max DD C nicht schlimmer als SPY (USD und CHF)",
              all(lv[c].max_drawdown >= sp[c].max_drawdown for c in ("USD", "CHF")),
              f"USD {lv['USD'].max_drawdown:.1%} vs {sp['USD'].max_drawdown:.1%}, "
              f"CHF {lv['CHF'].max_drawdown:.1%} vs {sp['CHF'].max_drawdown:.1%}"),
        Check("F3", f"in ≥ {MIN_MONTHS_BETTER} von {TEST_MONTHS} Monaten ≥ Mischung",
              better_months >= MIN_MONTHS_BETTER, f"bisher {better_months} von {n_months}"),
        Check("F4", f"live höchstens {MAX_BACKTEST_GAP:.0%}-Punkte vom Backtest entfernt",
              abs(gap) <= MAX_BACKTEST_GAP, f"Abweichung {gap:+.1%}"),
        Check("F5", "keine technischen Fehler", not problems, "; ".join(problems) or "keine gefunden"),
    ]
    estimated = sum(1 for f in fills if getattr(f, "estimated", False))
    return ForwardReport(start, end, len(days), months_done, finished, rows, monthly, checks, estimated)
