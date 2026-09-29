"""Backtest der Grid/DCA-Strategie auf Tagesbars – reine Logik, keine I/O.

Ausführungsmodell (bewusst vorsichtig):
- Einstieg: Market-Buy zum Eröffnungskurs des ersten Tages eines Zyklus.
- Limit-Buys: gefüllt, wenn das Tagestief den Level-Preis erreicht, zum Preis min(Open, Level).
- Take-Profit (optional): alles verkaufen, wenn das Tageshoch Ø-Einstand × (1 + TP) erreicht.
  Nicht am Einstiegstag und nicht an einem Tag, an dem nachgekauft wurde (Reihenfolge im Tag unbekannt).
- Stop-Loss (optional): sind alle Levels gekauft und fällt der Kurs weitere SL % unter das letzte Level,
  wird alles verkauft. Hat Vorrang vor Take-Profit.
- Trendfilter (optional): Einstieg und Nachkäufe nur, wenn der Schlusskurs des VORTAGS über seinem
  gleitenden Durchschnitt (SMA der N Schlusskurse davor) liegt – kein Blick in die Zukunft.
  Mit trend_exit wird zusätzlich zum Eröffnungskurs verkauft, sobald der Vortag unter dem SMA schloss.
- Nach einem Verkauf beginnt ein neuer Zyklus (ausser restart=False), sobald der Filter es erlaubt.
- Orders in festen $-Beträgen (Bruchteile erlaubt), Kommission pro Order.
Vergleich: Kaufen und Halten mit demselben Budget (Levels + 1) × Orderbetrag, am ersten Tag investiert.
"""
from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass, field

from quantdesk.history import Bar
from quantdesk.metrics import Perf, perf
from quantdesk.strategy import ValidationError, level_price, validate_grid

MAX_TREND_SMA = 250


@dataclass(frozen=True)
class GridParams:
    levels: int
    drawdown: float  # 0.02 = 2 %
    take_profit: float | None = None
    stop_loss: float | None = None
    restart: bool = True
    trend_sma: int | None = None  # z.B. 200 = nur über dem 200-Tage-Durchschnitt handeln
    trend_exit: bool = False

    def __post_init__(self):
        validate_grid(self.levels, self.drawdown)
        for name in ("take_profit", "stop_loss"):
            value = getattr(self, name)
            if value is not None and not value > 0:
                raise ValidationError(f"{name} muss > 0 sein.")
        if self.trend_sma is not None and not 2 <= self.trend_sma <= MAX_TREND_SMA:
            raise ValidationError(f"Trend-SMA muss zwischen 2 und {MAX_TREND_SMA} Tagen liegen.")
        if self.trend_exit and self.trend_sma is None:
            raise ValidationError("Trend-Exit braucht einen Trendfilter (trend_sma).")

    def label(self) -> str:
        parts = [f"{self.levels}×{self.drawdown:.0%}"]
        parts.append(f"TP {self.take_profit:.0%}" if self.take_profit else "ohne TP")
        if self.stop_loss:
            parts.append(f"SL {self.stop_loss:.0%}")
        if self.trend_sma:
            parts.append(f"Trend SMA{self.trend_sma}" + (" + Exit" if self.trend_exit else ""))
        if not self.restart:
            parts.append("1 Zyklus")
        return ", ".join(parts)


@dataclass(frozen=True)
class Costs:
    order_usd: float = 1000.0
    fee: float = 1.0


@dataclass
class Result:
    symbol: str
    params: GridParams
    start: str
    end: str
    budget: float
    pnl: float = 0.0
    realized: float = 0.0
    max_capital: float = 0.0
    max_drawdown: float = 0.0
    cycles: int = 0
    buys: int = 0
    sells: int = 0
    fees: float = 0.0
    bars_in_market: int = 0
    bars: int = 0
    open_position: bool = False
    hold_pnl: float = 0.0
    equity: list[float] = field(default_factory=list, repr=False)  # Gewinn/Verlust pro Tag
    days: list[str] = field(default_factory=list, repr=False)
    account: list[float] = field(default_factory=list, repr=False)  # Budget + Gewinn/Verlust pro Tag
    invested: list[float] = field(default_factory=list, repr=False)  # Wert der Position pro Tag
    hold_account: list[float] = field(default_factory=list, repr=False)

    @property
    def perf(self) -> Perf:
        return perf(self.account, self.invested)

    @property
    def hold_perf(self) -> Perf:
        return perf(self.hold_account, self.hold_account)

    @property
    def excess(self) -> float:
        """Gewinn der Strategie minus Gewinn von Kaufen und Halten."""
        return self.pnl - self.hold_pnl

    @property
    def excess_pct(self) -> float:
        """Differenz zu Kaufen und Halten in % des Budgets (vergleichbar über verschiedene Levels)."""
        return self.excess / self.budget

    @property
    def years(self) -> float:
        return max(self.bars / 252, 1 / 252)

    @property
    def cagr(self) -> float:
        """Jahresrendite auf das Budget."""
        return (max(self.budget + self.pnl, 0) / self.budget) ** (1 / self.years) - 1

    @property
    def hold_cagr(self) -> float:
        return (max(self.budget + self.hold_pnl, 0) / self.budget) ** (1 / self.years) - 1

    @property
    def time_in_market(self) -> float:
        return self.bars_in_market / self.bars if self.bars else 0.0


@dataclass(frozen=True)
class Summary:
    n: int
    pnl: float
    hold_pnl: float
    wins: int  # besser als Halten
    losers: int  # Strategie mit Verlust
    median_excess_pct: float

    @property
    def excess(self) -> float:
        return self.pnl - self.hold_pnl


def summarize(results: list[Result]) -> Summary:
    return Summary(
        n=len(results),
        pnl=sum(r.pnl for r in results),
        hold_pnl=sum(r.hold_pnl for r in results),
        wins=sum(r.excess > 0 for r in results),
        losers=sum(r.pnl < 0 for r in results),
        median_excess_pct=statistics.median(r.excess_pct for r in results) if results else 0.0,
    )


def buy_and_hold(bars: list[Bar], budget: float, fee: float) -> float:
    shares = (budget - fee) / bars[0].open
    return shares * bars[-1].close - fee - budget


def trend_ok_series(bars: list[Bar], sma: int | None) -> list[bool]:
    """ok[i] = Schlusskurs von Tag i-1 liegt über dem SMA der `sma` Schlusskurse vor Tag i-1."""
    if sma is None:
        return [True] * len(bars)
    ok = [False] * len(bars)
    window = 0.0
    for i in range(1, len(bars)):
        prev = i - 1  # letzter bekannter Schlusskurs zu Handelsbeginn an Tag i
        if prev >= sma:
            if prev == sma:
                window = sum(b.close for b in bars[:sma])
            else:
                window += bars[prev - 1].close - bars[prev - 1 - sma].close
            ok[i] = bars[prev].close > window / sma
    return ok


def simulate(
    bars: list[Bar],
    params: GridParams,
    costs: Costs = Costs(),
    symbol: str = "",
    start: int = 0,
    end: int | None = None,
) -> Result:
    """Handelt auf bars[start:end]; Bars davor dienen nur als Vorlauf für den Trendfilter."""
    end = len(bars) if end is None else end
    if end - start < 2:
        raise ValueError("Zu wenige Kursdaten für einen Backtest.")
    budget = (params.levels + 1) * costs.order_usd
    res = Result(symbol, params, bars[start].day, bars[end - 1].day, budget, bars=end - start)
    trend_ok = trend_ok_series(bars[:end], params.trend_sma)
    shares = cost = 0.0  # cost = investiertes Geld inkl. Kaufgebühren im laufenden Zyklus
    entry_bar = -1
    pending: list[float] = []  # Level-Preise, die noch nicht gekauft sind
    last_level = 0.0
    in_position = stopped = False
    peak = 0.0
    hold_shares = (budget - costs.fee) / bars[start].open

    def buy(price: float) -> None:
        nonlocal shares, cost
        shares += costs.order_usd / price
        cost += costs.order_usd + costs.fee
        res.buys += 1
        res.fees += costs.fee

    def sell(price: float) -> None:
        nonlocal shares, cost, in_position, stopped
        res.realized += shares * price - costs.fee - cost
        res.fees += costs.fee
        res.sells += 1
        shares = cost = 0.0
        in_position = False
        stopped = not params.restart

    for i in range(start, end):
        bar = bars[i]
        ok = trend_ok[i]
        if in_position and params.trend_exit and not ok and i > entry_bar:
            sell(bar.open)
        elif not in_position and not stopped and ok:
            entry_bar = i
            pending = [level_price(bar.open, params.drawdown, k) for k in range(1, params.levels + 1)]
            last_level = pending[-1]
            in_position = True
            buy(bar.open)
            res.cycles += 1

        if in_position:
            res.bars_in_market += 1
            bought_today = False
            if ok:  # kein Nachkaufen im Abwärtstrend
                still = []
                for price in pending:
                    if bar.low <= price:
                        buy(min(bar.open, price))
                        bought_today = True
                    else:
                        still.append(price)
                pending = still
            res.max_capital = max(res.max_capital, cost)

            exit_price = None
            if params.stop_loss and not pending:
                stop = last_level * (1 - params.stop_loss)
                if bar.low <= stop:
                    exit_price = min(bar.open, stop)
            if exit_price is None and params.take_profit and i > entry_bar and not bought_today:
                target = cost / shares * (1 + params.take_profit)
                if bar.high >= target:
                    exit_price = max(bar.open, target)
            if exit_price is not None:
                sell(exit_price)

        equity = res.realized + (shares * bar.close - cost if in_position else 0.0)
        res.equity.append(equity)
        res.days.append(bar.day)
        res.account.append(budget + equity)
        res.invested.append(shares * bar.close if in_position else 0.0)
        res.hold_account.append(hold_shares * bar.close)
        peak = max(peak, equity)
        res.max_drawdown = min(res.max_drawdown, equity - peak)

    res.pnl = res.equity[-1]
    res.open_position = in_position
    res.hold_pnl = buy_and_hold(bars[start:end], budget, costs.fee)
    return res


def halves(n_bars: int, start: int = 0, train_fraction: float = 0.5) -> tuple[range, range]:
    """Index-Bereiche für Training (1. Teil) und Test (2. Teil) ab `start`."""
    cut = start + int((n_bars - start) * train_fraction)
    return range(start, cut), range(cut, n_bars)


def default_grid() -> list[GridParams]:
    """Parameter-Raster für --sweep (nur gültige Kombinationen)."""
    out = []
    trends = ((None, False), (200, False), (200, True))
    for levels, dd, tp, sl, (sma, texit) in itertools.product(
        (3, 5, 10), (0.02, 0.05), (None, 0.05, 0.10, 0.20), (None, 0.10), trends
    ):
        try:
            out.append(GridParams(levels, dd, tp, sl, trend_sma=sma, trend_exit=texit))
        except ValidationError:
            pass
    return out
