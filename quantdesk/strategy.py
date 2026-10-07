"""Martingale/DCA-Grid aus dem Video – reine Logik, keine I/O.

Einstieg: 1 Market-Buy. Danach Limit-Buys unter dem Einstiegspreis:
    level_price[i] = entry_price * (1 - drawdown * i)   für i = 1..num_levels
Jedes Level wird genau einmal platziert.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

from quantdesk.broker.base import Order

MAX_LEVELS = 20
MAX_TREND_SMA = 250
PRICE_TOLERANCE = 0.01

PENDING, PLACED, FILLED, CANCELLED = "pending", "placed", "filled", "cancelled"
LEVEL_STATUSES = (PENDING, PLACED, FILLED, CANCELLED)
ON, OFF = "On", "Off"

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class ValidationError(ValueError):
    pass


def validate_grid(num_levels: int, drawdown: float) -> None:
    if not isinstance(num_levels, int) or num_levels < 1:
        raise ValidationError("Levels muss eine ganze Zahl ≥ 1 sein.")
    if num_levels > MAX_LEVELS:
        raise ValidationError(f"Maximal {MAX_LEVELS} Levels pro Symbol.")
    if not drawdown > 0:
        raise ValidationError("Drawdown % muss grösser als 0 sein.")
    if drawdown * num_levels >= 1:
        raise ValidationError(
            f"Levels × Drawdown muss unter 100 % bleiben (jetzt {drawdown * num_levels:.0%}) – "
            "sonst entstehen Preise ≤ 0."
        )


def parse_grid_input(symbol: str, levels: str, drawdown_pct: str) -> tuple[str, int, float]:
    """Formular-Eingaben prüfen. Drawdown kommt in Prozent (z.B. '2' oder '2,5')."""
    sym = (symbol or "").strip().upper()
    if not _SYMBOL_RE.match(sym):
        raise ValidationError("Ungültiges Symbol (z.B. AAPL).")
    try:
        num_levels = int(str(levels).strip())
    except ValueError:
        raise ValidationError("Levels muss eine ganze Zahl sein.") from None
    try:
        drawdown = float(str(drawdown_pct).strip().replace(",", ".")) / 100
    except ValueError:
        raise ValidationError("Drawdown % muss eine Zahl sein (z.B. 2).") from None
    validate_grid(num_levels, drawdown)
    return sym, num_levels, drawdown


def level_price(entry_price: float, drawdown: float, i: int) -> float:
    return round(entry_price * (1 - drawdown * i), 2)


@dataclass
class Level:
    level: int
    price: float
    status: str = PENDING
    order_id: str | None = None
    qty: float | None = None  # gefüllte Stückzahl (für den Abgleich mit der Position)

    @classmethod
    def from_dict(cls, d: dict) -> "Level":
        status = d.get("status", PENDING)
        if status not in LEVEL_STATUSES:
            raise ValueError(f"Unbekannter Level-Status: {status}")
        order_id = d.get("order_id")
        qty = d.get("qty")
        return cls(int(d["level"]), float(d["price"]), status, str(order_id) if order_id is not None else None,
                   float(qty) if qty is not None else None)


def build_levels(entry_price: float, num_levels: int, drawdown: float) -> list[Level]:
    validate_grid(num_levels, drawdown)
    if not entry_price > 0:
        raise ValidationError("Einstiegspreis muss > 0 sein.")
    return [Level(i, level_price(entry_price, drawdown, i)) for i in range(1, num_levels + 1)]


@dataclass
class EquitySystem:
    symbol: str
    num_levels: int
    drawdown: float
    status: str = OFF
    position: float = 0.0
    entry_price: float | None = None
    entry_order_id: str | None = None
    entry_order_time: float | None = None
    levels: list[Level] = field(default_factory=list)
    simulated: bool = False  # Zustand stammt aus DRY_RUN (überlebt keinen Neustart)
    # Kandidat C (Trendfilter wie im Backtest): Einstieg/Nachkäufe nur, wenn der Vortag über dem SMA schloss;
    # mit trend_exit wird alles verkauft, sobald der Vortag darunter schloss
    trend_sma: int | None = None
    trend_exit: bool = False
    order_usd: float | None = None  # Ordergrösse in $ (wie im Backtest); None = feste Stückzahl aus .env
    exit_order_id: str | None = None
    exit_order_time: float | None = None
    entry_qty: float | None = None  # Stückzahl des Einstiegs
    entry_ts: float | None = None  # Zeitpunkt des Einstiegs (für geschätzte Fill-Tage)

    def __post_init__(self):
        validate_grid(self.num_levels, self.drawdown)
        if self.trend_sma is not None and not 2 <= self.trend_sma <= MAX_TREND_SMA:
            raise ValidationError(f"Trend-SMA muss zwischen 2 und {MAX_TREND_SMA} Tagen liegen.")
        if self.trend_exit and self.trend_sma is None:
            raise ValidationError("Trend-Exit braucht einen Trendfilter.")
        if self.order_usd is not None and not self.order_usd > 0:
            raise ValidationError("Orderbetrag muss > 0 sein.")

    @property
    def trend_label(self) -> str:
        if self.trend_sma is None:
            return ""
        return f"SMA{self.trend_sma}" + (" + Exit" if self.trend_exit else "")

    @property
    def is_on(self) -> bool:
        return self.status == ON

    def ensure_levels(self) -> None:
        """Levels genau einmal aus dem Einstiegspreis erzeugen – sie wachsen nie."""
        if self.entry_price is not None and not self.levels:
            self.levels = build_levels(self.entry_price, self.num_levels, self.drawdown)

    def pending_levels(self) -> list[Level]:
        return [lv for lv in self.levels if lv.status == PENDING]

    def open_order_ids(self) -> list[str]:
        ids = [lv.order_id for lv in self.levels if lv.status == PLACED and lv.order_id]
        if self.entry_order_id and self.entry_price is None:
            ids.append(self.entry_order_id)
        if self.exit_order_id:
            ids.append(self.exit_order_id)
        return ids

    def reset_trading_state(self) -> None:
        """Konfiguration behalten, Einstieg und Levels vergessen."""
        self.position = 0.0
        self.entry_price = None
        self.entry_order_id = None
        self.entry_order_time = None
        self.levels = []
        self.simulated = False
        self.exit_order_id = None
        self.exit_order_time = None
        self.entry_qty = None
        self.entry_ts = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EquitySystem":
        levels = d.get("levels") or []
        if not isinstance(levels, list):
            raise ValueError("'levels' muss eine Liste sein (altes Format aus dem Video wird nicht unterstützt).")
        entry = d.get("entry_price")
        return cls(
            symbol=str(d["symbol"]).upper(),
            num_levels=int(d["num_levels"]),
            drawdown=float(d["drawdown"]),
            status=ON if d.get("status") == ON else OFF,
            position=float(d.get("position") or 0),
            entry_price=float(entry) if entry is not None else None,
            entry_order_id=d.get("entry_order_id"),
            entry_order_time=d.get("entry_order_time"),
            levels=[Level.from_dict(x) for x in levels],
            simulated=bool(d.get("simulated", False)),
            trend_sma=int(d["trend_sma"]) if d.get("trend_sma") else None,
            trend_exit=bool(d.get("trend_exit", False)),
            order_usd=float(d["order_usd"]) if d.get("order_usd") else None,
            exit_order_id=d.get("exit_order_id"),
            exit_order_time=d.get("exit_order_time"),
            entry_qty=float(d["entry_qty"]) if d.get("entry_qty") is not None else None,
            entry_ts=d.get("entry_ts"),
        )

    def accounted_qty(self) -> float | None:
        """Stückzahl laut eigener Buchführung (Einstieg + gefüllte Levels). None = Einstieg unbekannt."""
        if self.entry_qty is None:
            return None
        return self.entry_qty + sum(lv.qty or 0.0 for lv in self.levels if lv.status == FILLED)


def find_level_order(level: Level, symbol: str, orders: Iterable[Order], tol: float = PRICE_TOLERANCE) -> Order | None:
    """Order zu einem Level finden: zuerst über order_id, sonst Symbol + BUY LMT + Preis ±tol (nur offene)."""
    orders = list(orders)
    if level.order_id:
        for o in orders:
            if o.order_id == level.order_id:
                return o
    for o in orders:
        if (
            o.is_open
            and o.symbol == symbol
            and o.side == "BUY"
            and o.limit_price is not None
            and abs(o.limit_price - level.price) <= tol + 1e-9
        ):
            return o
    return None
