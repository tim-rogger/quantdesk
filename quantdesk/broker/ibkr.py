"""REST-Client für das IBKR Client Portal Gateway (nur Paper-Konten)."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from quantdesk.config import PAPER_ACCOUNT_PREFIX
from quantdesk.broker.base import (
    LOGIN_HINT,
    Broker,
    BrokerError,
    Execution,
    NotAuthenticatedError,
    Order,
    OrderResult,
    Position,
)

log = logging.getLogger(__name__)

MAX_REPLY_CONFIRMATIONS = 5
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def parse_snapshot_price(value: Any) -> float | None:
    """IBKR liefert Preise teils mit Präfix: 'C213.25' (Close), 'H213.25' (halted)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = str(value).strip().lstrip("CH").replace(",", "")
    try:
        price = float(text)
    except ValueError:
        return None
    return price if price > 0 else None


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_side(side: Any) -> str:
    text = str(side or "").strip().upper()
    return {"B": "BUY", "S": "SELL"}.get(text, text)


def parse_executions(raw: Any, account_id: str) -> list[Execution]:
    """Antwort von /iserver/account/trades -> Ausführungen dieses Kontos."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        acct = str(item.get("account") or item.get("accountCode") or account_id).upper()
        ts_ms = _to_float(item.get("trade_time_r"))
        if acct != account_id.upper() or item.get("order_id") in (None, "") or ts_ms is None:
            continue
        out.append(
            Execution(
                exec_id=str(item.get("execution_id")),
                order_id=str(item.get("order_id")),
                symbol=str(item.get("symbol") or "").upper(),
                side=_normalize_side(item.get("side")),
                qty=_to_float(item.get("size"), 0.0),
                price=_to_float(item.get("price"), 0.0),
                ts=ts_ms / 1000,
            )
        )
    return out


class IbkrClient(Broker):
    def __init__(
        self,
        base_url: str,
        account_id: str,
        session: requests.Session | None = None,
        timeout: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        account_id = (account_id or "").strip().upper()
        if not account_id.startswith(PAPER_ACCOUNT_PREFIX):
            raise ValueError(
                f"Konto '{account_id}' abgelehnt: nur IBKR-Paper-Konten ('{PAPER_ACCOUNT_PREFIX}...') sind erlaubt."
            )
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self.timeout = timeout
        self._sleep = sleep
        self._session = session or requests.Session()
        host = urlparse(self.base_url).hostname or ""
        # Das Gateway nutzt ein self-signed Zertifikat – nur lokal ohne Prüfung.
        self._verify = host not in LOCAL_HOSTS
        if not self._verify:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._conids: dict[str, int] = {}
        self._iserver_ready = False
        self._portfolio_ready = False
        self._lock = threading.Lock()  # requests.Session ist nicht garantiert thread-safe

    # ------------------------------------------------------------------ HTTP
    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        try:
            with self._lock:
                resp = self._session.request(method, url, timeout=self.timeout, verify=self._verify, **kwargs)
        except requests.RequestException as e:
            raise BrokerError(f"IBKR Gateway nicht erreichbar ({e.__class__.__name__}). Läuft das Gateway?") from e
        if resp.status_code == 401:
            raise NotAuthenticatedError(f"IBKR: nicht eingeloggt. {LOGIN_HINT}")
        if resp.status_code >= 400:
            raise BrokerError(f"IBKR {method} {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as e:
            raise BrokerError(f"IBKR {path}: keine gültige JSON-Antwort") from e

    # --------------------------------------------------------------- Session
    def auth_status(self) -> dict:
        data = self._request("POST", "/iserver/auth/status") or {}
        return {
            "authenticated": bool(data.get("authenticated")),
            "connected": bool(data.get("connected")),
            "competing": bool(data.get("competing")),
            "message": data.get("message", ""),
        }

    def keepalive(self) -> None:
        self._request("POST", "/tickle")

    @staticmethod
    def _account_ids(data: Any) -> set[str]:
        items = data.get("accounts", []) if isinstance(data, dict) else (data or [])
        ids = set()
        for item in items:
            if isinstance(item, str):
                ids.add(item.upper())
            elif isinstance(item, dict):
                for key in ("accountId", "id"):
                    if item.get(key):
                        ids.add(str(item[key]).upper())
        return ids

    def _check_account_listed(self, data: Any, endpoint: str) -> None:
        ids = self._account_ids(data)
        if ids and self.account_id not in ids:
            raise BrokerError(
                f"Konto {self.account_id} ist im Gateway-Login nicht vorhanden ({endpoint}: {sorted(ids)}). "
                "Bist du mit dem Paper-Login angemeldet?"
            )

    def _ensure_iserver(self) -> None:
        if not self._iserver_ready:
            self._check_account_listed(self._request("GET", "/iserver/accounts"), "/iserver/accounts")
            self._iserver_ready = True

    def _ensure_portfolio(self) -> None:
        if not self._portfolio_ready:
            self._check_account_listed(self._request("GET", "/portfolio/accounts"), "/portfolio/accounts")
            self._portfolio_ready = True

    # ------------------------------------------------------------- Contracts
    def conid(self, symbol: str) -> int:
        symbol = symbol.upper()
        if symbol in self._conids:
            return self._conids[symbol]
        data = self._request("GET", "/iserver/secdef/search", params={"symbol": symbol, "secType": "STK"})
        for item in data if isinstance(data, list) else []:
            conid = item.get("conid")
            if conid not in (None, "", -1, "-1"):
                self._conids[symbol] = int(conid)
                return self._conids[symbol]
        raise BrokerError(f"IBKR kennt das Symbol {symbol} nicht (keine conid).")

    # ------------------------------------------------------------ Marktdaten
    def get_last_price(self, symbol: str) -> float | None:
        conid = self.conid(symbol)
        for attempt in range(2):  # erster Snapshot-Aufruf ist oft leer
            data = self._request("GET", "/iserver/marketdata/snapshot", params={"conids": conid, "fields": "31"})
            if isinstance(data, list) and data:
                price = parse_snapshot_price(data[0].get("31"))
                if price is not None:
                    return price
            if attempt == 0:
                self._sleep(1.0)
        return None

    # -------------------------------------------------------------- Portfolio
    def get_positions(self) -> list[Position]:
        self._ensure_portfolio()
        data = self._request("GET", f"/portfolio/{self.account_id}/positions/0") or []
        positions = []
        for item in data:
            qty = _to_float(item.get("position"), 0.0)
            if not qty:
                continue
            symbol = (item.get("ticker") or item.get("contractDesc") or "").upper()
            avg = _to_float(item.get("avgPrice"))
            if avg is None:
                avg = _to_float(item.get("avgCost"), 0.0)
            conid = item.get("conid")
            positions.append(
                Position(
                    symbol=symbol,
                    qty=qty,
                    avg_price=round(avg, 4),
                    market_price=_to_float(item.get("mktPrice")),
                    unrealized_pnl=_to_float(item.get("unrealizedPnl")),
                    conid=int(conid) if conid else None,
                )
            )
        return positions

    def get_cash(self) -> float | None:
        self._ensure_portfolio()
        data = self._request("GET", f"/portfolio/{self.account_id}/ledger") or {}
        return _to_float((data.get("BASE") or {}).get("cashbalance"))

    # ----------------------------------------------------------------- Orders
    def get_orders(self) -> list[Order]:
        self._ensure_iserver()
        raw: list = []
        for attempt in range(2):  # erster Aufruf nach Login ist oft leer
            data = self._request("GET", "/iserver/account/orders") or {}
            raw = data.get("orders", []) if isinstance(data, dict) else []
            if raw:
                break
            if attempt == 0:
                self._sleep(0.5)
        orders = []
        for item in raw:
            acct = str(item.get("acct") or item.get("account") or self.account_id).upper()
            if acct != self.account_id:
                continue
            conid = item.get("conid")
            orders.append(
                Order(
                    order_id=str(item.get("orderId")),
                    symbol=str(item.get("ticker") or "").upper(),
                    side=_normalize_side(item.get("side")),
                    qty=_to_float(item.get("totalSize"), 0.0),
                    order_type=str(item.get("orderType") or "").upper().replace("LIMIT", "LMT").replace("MARKET", "MKT"),
                    limit_price=_to_float(item.get("price")),
                    status=str(item.get("status") or ""),
                    conid=int(conid) if conid else None,
                    avg_fill_price=_to_float(item.get("avgPrice")),
                )
            )
        return orders

    def get_executions(self, days: int = 7) -> list[Execution]:
        """Ausführungen der letzten `days` Tage (IBKR liefert höchstens 7). Erster Aufruf ist oft leer."""
        self._ensure_iserver()
        raw: list = []
        for attempt in range(2):
            data = self._request("GET", "/iserver/account/trades", params={"days": min(max(days, 1), 7)})
            raw = data if isinstance(data, list) else []
            if raw:
                break
            if attempt == 0:
                self._sleep(0.5)
        return parse_executions(raw, self.account_id)

    def place_market_order(self, symbol: str, side: str, qty: int) -> OrderResult:
        return self._place(symbol, {"orderType": "MKT", "side": side.upper(), "quantity": qty, "tif": "DAY"})

    def place_limit_order(self, symbol: str, side: str, qty: int, price: float) -> OrderResult:
        return self._place(
            symbol,
            {"orderType": "LMT", "price": round(price, 2), "side": side.upper(), "quantity": qty, "tif": "GTC"},
        )

    def _place(self, symbol: str, order: dict) -> OrderResult:
        self._ensure_iserver()
        body = {"orders": [{"conid": self.conid(symbol), **order}]}
        resp = self._request("POST", f"/iserver/account/{self.account_id}/orders", json=body)
        return self._confirm_replies(resp)

    def _confirm_replies(self, resp: Any) -> OrderResult:
        """IBKR fragt oft nach ('Are you sure...?') – bis zu 5x bestätigen."""
        messages: list[str] = []
        for attempt in range(MAX_REPLY_CONFIRMATIONS + 1):
            if isinstance(resp, dict):
                if resp.get("error"):
                    raise BrokerError(f"IBKR hat die Order abgelehnt: {resp['error']}")
                resp = [resp]
            if not isinstance(resp, list) or not resp:
                raise BrokerError(f"Unerwartete Order-Antwort von IBKR: {resp!r}")
            first = resp[0]
            if first.get("error"):
                raise BrokerError(f"IBKR hat die Order abgelehnt: {first['error']}")
            if first.get("order_id"):
                return OrderResult(str(first["order_id"]), str(first.get("order_status", "")), messages)
            reply_id = first.get("id")
            if not reply_id:
                raise BrokerError(f"Unerwartete Order-Antwort von IBKR: {first!r}")
            msg = first.get("message") or []
            msg_list = msg if isinstance(msg, list) else [str(msg)]
            messages.extend(str(m) for m in msg_list)
            if attempt == MAX_REPLY_CONFIRMATIONS:
                break
            log.info("IBKR-Rückfrage bestätigt: %s", " | ".join(str(m) for m in msg_list))
            resp =self._request("POST", f"/iserver/reply/{reply_id}", json={"confirmed": True})
        raise BrokerError(f"IBKR-Order nach {MAX_REPLY_CONFIRMATIONS} Bestätigungen nicht platziert: {messages}")

    def cancel_order(self, order_id: str) -> None:
        self._ensure_iserver()
        self._request("DELETE", f"/iserver/account/{self.account_id}/order/{order_id}")
