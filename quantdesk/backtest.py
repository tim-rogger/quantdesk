"""Backtest der Grid/DCA-Strategie auf Tagesbars – reine Logik, keine I/O.

Ausführungsmodell (bewusst vorsichtig):
- Einstieg: Market-Buy zum Eröffnungskurs des ersten Tages eines Zyklus.
- Limit-Buys: gefüllt, wenn das Tagestief den Level-Preis erreicht, zum Preis min(Open, Level).
- Take-Profit (optional): alles verkaufen, wenn das Tageshoch Ø-Einstand × (1 + TP) erreicht.
  Nicht am Einstiegstag und nicht an einem Tag, an dem nachgekauft wurde (Reihenfolge im Tag unbekannt).
- Stop-Loss (optional): sind alle Levels gekauft und fällt der Kurs weitere SL % unter das letzte Level,
  wird alles verkauft. Hat Vorrang vor Take-Profit.
- Nach einem Verkauf beginnt am nächsten Tag ein neuer Zyklus (ausser restart=False).
- Orders in festen $-Beträgen (Bruchteile erlaubt), Kommission pro Order.
Vergleich: Kaufen und Halten mit demselben Budget (Levels + 1) × Orderbetrag, am ersten Tag investiert.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from quantdesk.history import Bar
from quantdesk.strategy import ValidationError, level_price, validate_grid


@dataclass(frozen=True)
class GridParams:
    levels: int
    drawdown: float  # 0.02 = 2 %
    take_profit: float | None = None
    stop_loss: float | None = None
    restart: bool = True

    def __post_init__(self):
        validate_grid(self.levels, self.drawdown)
        for name in ("take_profit", "stop_loss"):
            value = getattr(self, name)
            if value is not None and not value > 0:
                raise ValidationError(f"{name} muss > 0 sein.")

    def label(self) -> str:
        parts = [f"{self.levels}×{self.drawdown:.0%}"]
        parts.append(f"TP {self.take_profit:.0%}" if self.take_profit else "ohne TP")
        if self.stop_loss:
            parts.append(f"SL {self.stop_loss:.0%}")
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
    equity: list[float] = field(default_factory=list, repr=False)

    @property
    def excess(self) -> float:
        """Gewinn der Strategie minus Gewinn von Kaufen und Halten."""
        return self.pnl - self.hold_pnl

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


def buy_and_hold(bars: list[Bar], budget: float, fee: float) -> float:
    shares = (budget - fee) / bars[0].open
    return shares * bars[-1].close - fee - budget


def simulate(bars: list[Bar], params: GridParams, costs: Costs = Costs(), symbol: str = "") -> Result:
    if len(bars) < 2:
        raise ValueError("Zu wenige Kursdaten für einen Backtest.")
    budget = (params.levels + 1) * costs.order_usd
    res = Result(symbol, params, bars[0].day, bars[-1].day, budget, bars=len(bars))
    shares = cost = 0.0  # cost = investiertes Geld inkl. Kaufgebühren im laufenden Zyklus
    entry: float | None = None
    entry_bar = -1
    filled: set[int] = set()
    stopped = False
    peak = 0.0

    def buy(price: float) -> None:
        nonlocal shares, cost
        shares += costs.order_usd / price
        cost += costs.order_usd + costs.fee
        res.buys += 1
        res.fees += costs.fee

    for i, bar in enumerate(bars):
        if entry is None and not stopped:
            entry, entry_bar, filled = bar.open, i, set()
            buy(bar.open)
            res.cycles += 1
        bought_today = False
        if entry is not None:
            res.bars_in_market += 1
            for k in range(1, params.levels + 1):
                price = level_price(entry, params.drawdown, k)
                if k not in filled and price > 0 and bar.low <= price:
                    buy(min(bar.open, price))
                    filled.add(k)
                    bought_today = True
            res.max_capital = max(res.max_capital, cost)

            exit_price = None
            if params.stop_loss and len(filled) == params.levels:
                stop = level_price(entry, params.drawdown, params.levels) * (1 - params.stop_loss)
                if bar.low <= stop:
                    exit_price = min(bar.open, stop)
            if exit_price is None and params.take_profit and i > entry_bar and not bought_today:
                target = cost / shares * (1 + params.take_profit)
                if bar.high >= target:
                    exit_price = max(bar.open, target)
            if exit_price is not None:
                res.realized += shares * exit_price - costs.fee - cost
                res.fees += costs.fee
                res.sells += 1
                shares = cost = 0.0
                entry = None
                stopped = not params.restart

        equity = res.realized + (shares * bar.close - cost if shares else 0.0)
        res.equity.append(equity)
        peak = max(peak, equity)
        res.max_drawdown = min(res.max_drawdown, equity - peak)

    res.pnl = res.equity[-1]
    res.open_position = shares > 0
    res.hold_pnl = buy_and_hold(bars, budget, costs.fee)
    return res


def split(bars: list[Bar], train_fraction: float = 0.5) -> tuple[list[Bar], list[Bar]]:
    cut = int(len(bars) * train_fraction)
    return bars[:cut], bars[cut:]


def default_grid() -> list[GridParams]:
    """Parameter-Raster für --sweep (nur gültige Kombinationen)."""
    out = []
    for levels, dd, tp, sl in itertools.product(
        (3, 5, 10), (0.01, 0.02, 0.03, 0.05, 0.08), (None, 0.03, 0.05, 0.10, 0.20), (None, 0.10, 0.20)
    ):
        try:
            out.append(GridParams(levels, dd, tp, sl))
        except ValidationError:
            pass
    return out


@dataclass
class SweepResult:
    best: GridParams
    train: list[Result]
    test: list[Result]
    ranking: list[tuple[float, GridParams]]  # (Überschuss Training, Parameter), bester zuerst


def sweep(
    data: dict[str, list[Bar]],
    grid: list[GridParams] | None = None,
    costs: Costs = Costs(),
    train_fraction: float = 0.5,
) -> SweepResult:
    """Beste Parameter NUR auf dem Trainingszeitraum wählen, dann auf dem Testzeitraum prüfen.

    Wie gut die Parameter im Training aussehen, sagt wenig – ehrlich ist nur das Test-Ergebnis.
    """
    grid = grid or default_grid()
    halves = {sym: split(bars, train_fraction) for sym, bars in data.items()}
    ranking = []
    for params in grid:
        excess = sum(simulate(train, params, costs, sym).excess for sym, (train, _) in halves.items())
        ranking.append((excess, params))
    ranking.sort(key=lambda x: x[0], reverse=True)
    best = ranking[0][1]
    return SweepResult(
        best,
        [simulate(train, best, costs, sym) for sym, (train, _) in halves.items()],
        [simulate(test, best, costs, sym) for sym, (_, test) in halves.items()],
        ranking,
    )
