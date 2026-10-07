"""Broker-Interface und gemeinsame Datentypen (unabhängig von IBKR)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# IBKR-Order-Status, gruppiert
OPEN_STATUSES = frozenset({"Submitted", "PreSubmitted", "PendingSubmit"})
FILLED_STATUSES = frozenset({"Filled"})
CANCELLED_STATUSES = frozenset({"Cancelled", "ApiCancelled", "Inactive"})

LOGIN_HINT = "Bitte im Browser https://localhost:5000 mit dem Paper-Login anmelden."


class BrokerError(Exception):
    """Jeder Fehler beim Broker (Netzwerk, HTTP, unerwartete Antwort)."""


class NotAuthenticatedError(BrokerError):
    """Gateway läuft, aber es ist niemand eingeloggt (oder die Session ist abgelaufen)."""


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    avg_price: float
    market_price: float | None = None
    unrealized_pnl: float | None = None
    conid: int | None = None
    simulated: bool = False  # True = nur im DRY_RUN simuliert

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "qty": self.qty,
            "avg_price": self.avg_price,
            "market_price": self.market_price,
            "unrealized_pnl": self.unrealized_pnl,
            "simulated": self.simulated,
        }


@dataclass(frozen=True)
class Order:
    order_id: str
    symbol: str
    side: str  # "BUY" / "SELL"
    qty: float
    order_type: str  # "MKT" / "LMT"
    limit_price: float | None
    status: str
    conid: int | None = None
    simulated: bool = False
    avg_fill_price: float | None = None  # Ausführungspreis ohne Kommission (nur bei Fills)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_filled(self) -> bool:
        return self.status in FILLED_STATUSES

    @property
    def is_cancelled(self) -> bool:
        return self.status in CANCELLED_STATUSES

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "type": self.order_type,
            "limit_price": self.limit_price,
            "status": self.status,
            "avg_fill_price": self.avg_fill_price,
            "simulated": self.simulated,
        }


@dataclass(frozen=True)
class Execution:
    """Eine Ausführung (Teil-Fill) einer Order, wie IBKR sie unter /iserver/account/trades meldet."""

    exec_id: str
    order_id: str
    symbol: str
    side: str  # "BUY" / "SELL"
    qty: float
    price: float
    ts: float  # Ausführungszeit (Unix-Sekunden, UTC)

    def to_dict(self) -> dict:
        return {"exec_id": self.exec_id, "order_id": self.order_id, "symbol": self.symbol, "side": self.side,
                "qty": self.qty, "price": self.price, "ts": self.ts}


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    status: str
    messages: list[str] = field(default_factory=list)


class Broker(ABC):
    """Alles, was Engine, Marktdaten und AI vom Broker brauchen."""

    @abstractmethod
    def auth_status(self) -> dict:
        """{'authenticated': bool, 'connected': bool, ...}"""

    @abstractmethod
    def keepalive(self) -> None:
        """Session am Leben halten (IBKR: /tickle)."""

    @abstractmethod
    def get_positions(self) -> list[Position]:
        ...

    @abstractmethod
    def get_cash(self) -> float | None:
        ...

    @abstractmethod
    def get_orders(self) -> list[Order]:
        """Alle bekannten Orders (offen, gefüllt, storniert)."""

    @abstractmethod
    def get_last_price(self, symbol: str) -> float | None:
        ...

    @abstractmethod
    def place_market_order(self, symbol: str, side: str, qty: int) -> OrderResult:
        ...

    @abstractmethod
    def place_limit_order(self, symbol: str, side: str, qty: int, price: float) -> OrderResult:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        ...

    def get_open_orders(self) -> list[Order]:
        return [o for o in self.get_orders() if o.is_open]

    def get_executions(self, days: int = 7) -> list[Execution]:
        """Ausführungen der letzten Tage (auch von Orders früherer Sitzungen). Standard: keine."""
        return []
