"""Test-Doubles: FakeBroker (In-Memory) und FakeMarketData – kein Netzwerk."""
from __future__ import annotations

import itertools

from quantdesk.broker.base import Broker, BrokerError, Order, OrderResult, Position
from quantdesk.marketdata import Quote


class FakeBroker(Broker):
    def __init__(self, price: float = 100.0):
        self.price = price
        self.positions: dict[str, Position] = {}
        self.orders: dict[str, Order] = {}
        self.placed: list[tuple] = []
        self.cancelled: list[str] = []
        self.authenticated = True
        self.fail_reads = False
        self.fail_orders = False
        self.cash = 100_000.0
        self.executions: list = []  # Ausführungen, wie /iserver/account/trades sie liefert
        self.execution_calls = 0
        self._ids = itertools.count(1)

    # Lesen
    def auth_status(self) -> dict:
        return {"authenticated": self.authenticated, "connected": True}

    def keepalive(self) -> None:
        pass

    def get_positions(self):
        if self.fail_reads:
            raise BrokerError("Netzwerkfehler (Test)")
        return list(self.positions.values())

    def get_cash(self):
        return self.cash

    def get_orders(self):
        if self.fail_reads:
            raise BrokerError("Netzwerkfehler (Test)")
        return list(self.orders.values())

    def get_last_price(self, symbol):
        return self.price

    def get_executions(self, days=7):
        self.execution_calls += 1
        return list(self.executions)

    # Schreiben
    def _add(self, symbol, side, qty, order_type, price):
        if self.fail_orders:
            raise BrokerError("Order abgelehnt (Test)")
        oid = f"O{next(self._ids)}"
        self.orders[oid] = Order(oid, symbol, side, float(qty), order_type, price, "Submitted")
        self.placed.append((order_type, symbol, side, qty, price))
        return OrderResult(oid, "Submitted")

    def place_market_order(self, symbol, side, qty):
        return self._add(symbol, side, qty, "MKT", None)

    def place_limit_order(self, symbol, side, qty, price):
        return self._add(symbol, side, qty, "LMT", price)

    def cancel_order(self, order_id):
        if order_id not in self.orders:
            raise BrokerError(f"Order {order_id} unbekannt (Test)")
        self.cancelled.append(order_id)
        o = self.orders[order_id]
        self.orders[order_id] = Order(o.order_id, o.symbol, o.side, o.qty, o.order_type, o.limit_price, "Cancelled")

    # Test-Helfer
    def fill(self, order_id: str, price: float, commission: float = 0.0) -> None:
        """Wie IBKR: Order kennt den Ausführungspreis, die Position rechnet die Kommission ein."""
        o = self.orders[order_id]
        self.orders[order_id] = Order(
            o.order_id, o.symbol, o.side, o.qty, o.order_type, o.limit_price, "Filled", avg_fill_price=price
        )
        old = self.positions.get(o.symbol)
        qty = (old.qty if old else 0) + o.qty
        cost = ((old.qty * old.avg_price) if old else 0) + o.qty * price + commission
        self.positions[o.symbol] = Position(o.symbol, qty, cost / qty)

    def orders_of_type(self, order_type: str) -> list[Order]:
        return [o for o in self.orders.values() if o.order_type == order_type]


class FakeMarketData:
    def __init__(self, price: float = 100.0):
        self.price = price

    def get_quote(self, symbol):
        return Quote(symbol, self.price, "Test")

    def get_price(self, symbol):
        return self.price
