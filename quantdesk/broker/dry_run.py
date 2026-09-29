"""DRY_RUN: echte Daten lesen, Orders nur loggen und lokal simulieren – nichts geht an IBKR."""
from __future__ import annotations

import itertools
import logging
import threading
from typing import Callable

from quantdesk.broker.base import Broker, BrokerError, Order, OrderResult, Position

log = logging.getLogger(__name__)


class OfflineBroker(Broker):
    """Platzhalter ohne Gateway (DRY_RUN ohne IBKR_ACCOUNT_ID): leeres Konto, Preise kommen von Stooq."""

    def auth_status(self) -> dict:
        return {"authenticated": True, "connected": True, "offline": True}

    def keepalive(self) -> None:
        pass

    def get_positions(self) -> list[Position]:
        return []

    def get_cash(self) -> float | None:
        return None

    def get_orders(self) -> list[Order]:
        return []

    def get_last_price(self, symbol: str) -> float | None:
        raise BrokerError("offline – kein IBKR-Snapshot")

    def place_market_order(self, symbol, side, qty):
        raise BrokerError("OfflineBroker sendet keine Orders")

    def place_limit_order(self, symbol, side, qty, price):
        raise BrokerError("OfflineBroker sendet keine Orders")

    def cancel_order(self, order_id):
        raise BrokerError("OfflineBroker storniert keine Orders")


class DryRunBroker(Broker):
    """Wrapper um einen echten Broker.

    Lesen geht an den echten Broker. Orders werden NICHT gesendet, sondern geloggt und als
    simulierte Orders/Positionen gemerkt, damit man den Ablauf des Grids in der GUI sieht:
    Market-Orders gelten sofort zum aktuellen Preis als gefüllt, Limit-Orders bleiben offen.
    """

    def __init__(
        self,
        inner: Broker,
        price_fn: Callable[[str], float | None],
        on_log: Callable[[str], None] | None = None,
    ):
        self.inner = inner
        self._price_fn = price_fn
        self._on_log = on_log
        self._ids = itertools.count(1)
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}
        self._lock = threading.Lock()

    def _log(self, msg: str) -> None:
        log.info(msg)
        if self._on_log:
            self._on_log(msg)

    # Lesen: echt + simuliert
    def auth_status(self) -> dict:
        return self.inner.auth_status()

    def keepalive(self) -> None:
        self.inner.keepalive()

    def get_positions(self) -> list[Position]:
        real = self.inner.get_positions()
        with self._lock:
            held = {p.symbol for p in real}
            return real + [p for s, p in self._positions.items() if s not in held]

    def get_cash(self) -> float | None:
        return self.inner.get_cash()

    def get_orders(self) -> list[Order]:
        real = self.inner.get_orders()
        with self._lock:
            return real + list(self._orders.values())

    def get_last_price(self, symbol: str) -> float | None:
        return self.inner.get_last_price(symbol)

    # Schreiben: nur simulieren
    def _new_id(self) -> str:
        return f"DRY-{next(self._ids)}"

    def place_market_order(self, symbol: str, side: str, qty: int) -> OrderResult:
        order_id = self._new_id()
        price = self._price_fn(symbol)
        with self._lock:
            if price is None:
                status = "PendingSubmit"
            else:
                status = "Filled"
                self._positions[symbol] = Position(symbol, float(qty), float(price), float(price), 0.0, simulated=True)
            self._orders[order_id] = Order(
                order_id, symbol, side.upper(), float(qty), "MKT", None, status, simulated=True,
                avg_fill_price=float(price) if price is not None else None,
            )
        where = f"@ ~{price:.2f} (simuliert gefüllt)" if price is not None else "(kein Preis – bleibt offen)"
        self._log(f"DRY_RUN: MKT {side.upper()} {qty} {symbol} {where} – NICHT gesendet")
        return OrderResult(order_id, status, ["DRY_RUN"])

    def place_limit_order(self, symbol: str, side: str, qty: int, price: float) -> OrderResult:
        order_id = self._new_id()
        with self._lock:
            self._orders[order_id] = Order(
                order_id, symbol, side.upper(), float(qty), "LMT", round(price, 2), "Submitted", simulated=True
            )
        self._log(f"DRY_RUN: LMT {side.upper()} {qty} {symbol} @ {price:.2f} GTC – NICHT gesendet")
        return OrderResult(order_id, "Submitted", ["DRY_RUN"])

    def cancel_order(self, order_id: str) -> None:
        with self._lock:
            order = self._orders.get(order_id)
            if order is not None:
                self._orders[order_id] = Order(
                    order.order_id, order.symbol, order.side, order.qty, order.order_type,
                    order.limit_price, "Cancelled", order.conid, simulated=True,
                )
        self._log(f"DRY_RUN: Storno {order_id} – NICHT gesendet")
