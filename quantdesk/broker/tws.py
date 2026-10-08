"""Broker-Adapter für IB Gateway / TWS über die TWS-API (Bibliothek ib_async) – für den Server-Betrieb.

- Verbindung zu IB Gateway (im Docker-Image gnzsnz/ib-gateway: Port 4004 = Paper), feste clientId.
- Sicherheit: Es wird nur ein Konto akzeptiert, das mit "DU" beginnt (Paper). Sonst Abbruch.
- Order-ID = permId (global eindeutig, gleich in offenen Orders, abgeschlossenen Orders und Ausführungen).
- IBKR-Fehler/Warnungen zu einer Order werden NICHT bestätigt, sondern gesammelt: Wird die Order abgelehnt,
  gilt sie als nicht platziert (BrokerError / NotTradableError); ist sie trotz Warnung aktiv, kommen die
  Meldungen in OrderResult.messages (Engine meldet sie weiter, z.B. per Push).
- Kurse: reqMarketDataType(3) = verzögerte Daten (Paper-Konten haben meist keine Echtzeit-Abos).

Nicht thread-sicher: für den headless Tageslauf (run_daily.py) gedacht. Die Tkinter-GUI nutzt weiter das Client Portal.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import time
from typing import Any

from quantdesk.broker.base import (
    Broker,
    BrokerError,
    Execution,
    NotTradableError,
    UnknownContractError,
    Order,
    OrderResult,
    Position,
    is_permission_error,
)
from quantdesk.config import PAPER_ACCOUNT_PREFIX

log = logging.getLogger(__name__)

# Reine Info-Meldungen der Verbindung (Marktdaten-Farm ok usw.) – kein Fehler
INFO_CODES = {2104, 2106, 2107, 2108, 2119, 2150, 2158}
# "Requested market data is not subscribed. Displaying delayed market data." – erwartet bei Paper ohne Abo
DELAYED_CODES = {10167, 10168, 354}
NO_CONTRACT_CODE = 200
# ib_async setzt nicht gesetzte Zahlen auf sys.float_info.max (UNSET_DOUBLE) bzw. 2**31-1 (UNSET_INTEGER).
# Alles darüber ist kein echter Wert (keine Aktie kostet 1e12 $, keine Order hat 1e12 Stück).
MAX_SANE = 1e12
LIVE = {"Submitted", "PreSubmitted", "Filled"}
DEAD = {"Cancelled", "ApiCancelled", "Inactive"}
WAIT_STATUS = {"", "PendingSubmit", "ApiPending"}


def _num(value: Any) -> float | None:
    """Positive, endliche, plausible Zahl – sonst None (auch für die ib_async-Platzhalter UNSET_DOUBLE/INTEGER)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f <= 0 or f >= MAX_SANE:
        return None
    return f


def _side(action: str) -> str:
    a = (action or "").upper()
    return {"BOT": "BUY", "SLD": "SELL"}.get(a, a)


def _ts(value: Any) -> float | None:
    if isinstance(value, dt.datetime):
        return value.timestamp() if value.tzinfo else value.replace(tzinfo=dt.timezone.utc).timestamp()
    return None


class TwsBroker(Broker):
    def __init__(
        self,
        host: str,
        port: int,
        client_id: int,
        account_id: str = "",
        ib: Any = None,
        timeout: float = 15.0,
        order_wait: float = 10.0,
        sleep=None,
    ):
        if ib is None:
            from ib_async import IB  # erst hier importieren: Tests und Laptop-Betrieb brauchen ib_async nicht

            ib = IB()
        self.ib = ib
        self.host, self.port, self.client_id = host, port, client_id
        self.account_id = (account_id or "").strip().upper()
        self.timeout = timeout
        self.order_wait = order_wait
        self._sleep = sleep or self.ib.sleep
        self._contracts: dict[str, Any] = {}
        self._errors: dict[int, list[tuple[int, str]]] = {}
        self.delayed_symbols: set[str] = set()  # Kurse nur verzögert (kein Echtzeit-Abo)
        self.unknown_symbols: set[str] = set()  # keine Kontraktdefinition bei IBKR
        self.ib.errorEvent += self._on_error
        # ib_async loggt jede IBKR-Meldung selbst als ERROR – das übernehmen wir gesammelt (siehe notices())
        logging.getLogger("ib_async.wrapper").setLevel(logging.CRITICAL)

    # ------------------------------------------------------------ Verbindung
    def _on_error(self, req_id, code, message, contract=None, *args) -> None:
        if code in INFO_CODES:
            return
        symbol = str(getattr(contract, "symbol", "") or "").replace(" ", ".").upper()
        if code in DELAYED_CODES:
            if symbol:
                self.delayed_symbols.add(symbol)
            return  # einmal pro Lauf zusammengefasst (notices)
        if code == NO_CONTRACT_CODE and symbol:
            self.unknown_symbols.add(symbol)
            return
        log.warning("IBKR %s (req %s): %s", code, req_id, message)
        self._errors.setdefault(int(req_id), []).append((int(code), str(message)))

    def notices(self) -> list[str]:
        """Zusammenfassung pro Lauf statt einer Logzeile pro Symbol."""
        out = []
        if self.delayed_symbols:
            out.append(f"Kurse von IBKR nur verzögert (kein Echtzeit-Abo) für {len(self.delayed_symbols)} Symbol(e).")
        if self.unknown_symbols:
            out.append(f"Keine Kontraktdefinition bei IBKR: {', '.join(sorted(self.unknown_symbols))}.")
        return out

    def _connect(self) -> None:
        if self.ib.isConnected():
            return
        try:
            self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=self.timeout)
        except (OSError, TimeoutError, ConnectionError) as e:
            raise BrokerError(f"IB Gateway nicht erreichbar ({self.host}:{self.port}): {e}") from e
        accounts = [a.upper() for a in self.ib.managedAccounts()]
        if not accounts:
            self.ib.disconnect()
            raise BrokerError("IB Gateway meldet kein Konto – Login abgeschlossen?")
        if not self.account_id:
            self.account_id = accounts[0]
        if self.account_id not in accounts or not self.account_id.startswith(PAPER_ACCOUNT_PREFIX):
            self.ib.disconnect()
            raise BrokerError(
                f"Konto abgelehnt: erlaubt sind nur Paper-Konten ('{PAPER_ACCOUNT_PREFIX}...'), "
                f"Gateway meldet {accounts}, konfiguriert ist '{self.account_id}'."
            )
        self.ib.reqMarketDataType(3)

    def auth_status(self) -> dict:
        self._connect()
        return {"authenticated": True, "connected": True, "account": self.account_id}

    def keepalive(self) -> None:
        pass  # ib_async hält die Verbindung selbst

    def close(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    # ------------------------------------------------------------ Kontrakte
    def _contract(self, symbol: str):
        symbol = symbol.upper()
        if symbol in self.unknown_symbols:
            raise UnknownContractError(f"IBKR kennt das Symbol {symbol} nicht (keine Kontraktdefinition).")
        if symbol not in self._contracts:
            from ib_async import Stock

            self._connect()
            qualified = [c for c in self.ib.qualifyContracts(Stock(symbol.replace(".", " "), "SMART", "USD")) if c]
            if not qualified or not getattr(qualified[0], "conId", 0):
                self.unknown_symbols.add(symbol)
                raise UnknownContractError(f"IBKR kennt das Symbol {symbol} nicht (keine Kontraktdefinition).")
            self._contracts[symbol] = qualified[0]
        return self._contracts[symbol]

    # ------------------------------------------------------------ Lesen
    def get_positions(self) -> list[Position]:
        self._connect()
        out = []
        for p in self.ib.positions(self.account_id):
            qty = float(p.position or 0)
            if qty and getattr(p.contract, "secType", "STK") == "STK":
                out.append(Position(p.contract.symbol.replace(" ", ".").upper(), qty, float(p.avgCost or 0), conid=p.contract.conId))
        return out

    def _account_value(self, tag: str) -> float | None:
        self._connect()
        for v in self.ib.accountValues(self.account_id):
            if v.tag == tag and v.currency == "BASE":
                return _num(v.value)
        return None

    def get_cash(self) -> float | None:
        return self._account_value("TotalCashBalance")

    def get_net_liquidation(self) -> float | None:
        return self._account_value("NetLiquidationByCurrency") or self._account_value("NetLiquidation")

    def _to_order(self, trade) -> Order | None:
        o, st = trade.order, trade.orderStatus
        perm = int(getattr(o, "permId", 0) or getattr(st, "permId", 0) or 0)
        if not perm:
            return None
        acct = (getattr(o, "account", "") or self.account_id).upper()
        if acct != self.account_id:
            return None
        fills = list(getattr(trade, "fills", []) or [])
        last_fill = max((_ts(f.time) or 0 for f in fills), default=0) or None
        # Gefüllte Menge: erst der Orderstatus, dann Order.filledQuantity – beide nur, wenn es echte Werte sind
        # (Order.filledQuantity ist bei offenen Orders der Platzhalter UNSET_DOUBLE = 1.8e308!)
        filled = _num(getattr(st, "filled", None)) or _num(getattr(o, "filledQuantity", None))
        status = st.status or ""  # der von IBKR gemeldete Status zählt, nie aus der Menge abgeleitet
        return Order(
            order_id=str(perm),
            symbol=trade.contract.symbol.replace(" ", ".").upper(),
            side=_side(o.action),
            qty=float(o.totalQuantity or 0),
            order_type={"MKT": "MKT", "LMT": "LMT"}.get(o.orderType, o.orderType),
            limit_price=_num(getattr(o, "lmtPrice", None)),
            status=status,
            conid=getattr(trade.contract, "conId", None),
            avg_fill_price=_num(getattr(st, "avgFillPrice", None)),
            filled_qty=filled,
            filled_at=last_fill,
        )

    def get_orders(self) -> list[Order]:
        """Offene Orders (auch von früheren Sitzungen) + abgeschlossene Orders (gefüllt/storniert)."""
        self._connect()
        seen: dict[str, Order] = {}
        for trade in list(self.ib.reqAllOpenOrders()) + list(self.ib.reqCompletedOrders(apiOnly=False)):
            order = self._to_order(trade)
            if order is not None and (order.order_id not in seen or order.status in ("Filled",) + tuple(DEAD)):
                seen[order.order_id] = order
        return list(seen.values())

    def get_executions(self, days: int = 7) -> list[Execution]:
        from ib_async import ExecutionFilter

        self._connect()
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y%m%d-%H:%M:%S")
        out = []
        for f in self.ib.reqExecutions(ExecutionFilter(acctCode=self.account_id, time=since)):
            e = f.execution
            if not getattr(e, "permId", 0):
                continue
            out.append(Execution(
                exec_id=str(e.execId), order_id=str(e.permId), symbol=f.contract.symbol.replace(" ", ".").upper(),
                side=_side(e.side), qty=float(e.shares), price=float(e.price),
                ts=_ts(getattr(e, "time", None)) or _ts(getattr(f, "time", None)) or time.time(),
            ))
        return out

    def get_last_price(self, symbol: str) -> float | None:
        contract = self._contract(symbol)
        tickers = self.ib.reqTickers(contract)
        if not tickers:
            return None
        t = tickers[0]
        for value in (getattr(t, "last", None), t.marketPrice() if hasattr(t, "marketPrice") else None,
                      getattr(t, "close", None)):
            price = _num(value)
            if price is not None:
                return price
        return None

    # ------------------------------------------------------------ Orders
    def place_market_order(self, symbol: str, side: str, qty: int) -> OrderResult:
        from ib_async import MarketOrder

        return self._place(symbol, MarketOrder(side.upper(), qty, tif="DAY", account=self.account_id))

    def place_limit_order(self, symbol: str, side: str, qty: int, price: float) -> OrderResult:
        from ib_async import LimitOrder

        return self._place(symbol, LimitOrder(side.upper(), qty, round(price, 2), tif="GTC", account=self.account_id))

    def _place(self, symbol: str, order) -> OrderResult:
        contract = self._contract(symbol)
        trade = self.ib.placeOrder(contract, order)
        oid = int(trade.order.orderId)
        deadline = time.monotonic() + self.order_wait
        while time.monotonic() < deadline:
            status = trade.orderStatus.status or ""
            if status not in WAIT_STATUS and (trade.order.permId or status in DEAD):
                break
            self._sleep(0.2)
        status = trade.orderStatus.status or ""
        messages = [f"{code}: {msg}" for code, msg in self._errors.pop(oid, [])]
        if status in DEAD or not trade.order.permId:
            text = "; ".join(messages) or f"Status {status or 'unbekannt'}"
            if status not in DEAD:
                try:  # nicht bestätigt -> zurückziehen, damit nichts unkontrolliert offen bleibt
                    self.ib.cancelOrder(trade.order)
                except Exception:  # pragma: no cover - best effort
                    pass
            cls = NotTradableError if is_permission_error(text) else BrokerError
            raise cls(f"IBKR hat die Order nicht angenommen ({symbol}): {text}")
        return OrderResult(str(trade.order.permId), status, messages)

    def cancel_order(self, order_id: str) -> None:
        self._connect()
        for trade in self.ib.reqAllOpenOrders():
            if str(trade.order.permId) == str(order_id):
                self.ib.cancelOrder(trade.order)
                return
        raise BrokerError(f"Order {order_id} ist bei IBKR nicht (mehr) offen.")
