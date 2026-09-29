import json

import pytest
import requests

from quantdesk.broker.base import BrokerError, NotAuthenticatedError
from quantdesk.broker.ibkr import MAX_REPLY_CONFIRMATIONS, IbkrClient, parse_snapshot_price
from quantdesk.config import validate_account_id

BASE = "https://localhost:5000/v1/api"
ACCT = "DU1234567"


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class FakeSession:
    """Antworten pro (Methode, Pfad) als Liste – jeder Aufruf nimmt die nächste (die letzte bleibt)."""

    def __init__(self, routes):
        self.routes = {k: list(v) if isinstance(v, list) and v and isinstance(v[0], FakeResponse) else [v]
                       for k, v in routes.items()}
        self.calls = []

    def request(self, method, url, **kwargs):
        path = url.replace(BASE, "")
        self.calls.append((method, path, kwargs))
        queue = self.routes.get((method, path))
        if queue is None:
            raise AssertionError(f"Unerwarteter Aufruf {method} {path}")
        resp = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(resp, Exception):
            raise resp
        return resp


def ok(body):
    return FakeResponse(200, body)


ACCOUNTS = {
    ("GET", "/iserver/accounts"): ok({"accounts": [ACCT], "selectedAccount": ACCT}),
    ("GET", "/portfolio/accounts"): ok([{"id": ACCT, "accountId": ACCT}]),
    ("GET", "/iserver/secdef/search"): ok([{"conid": "265598", "symbol": "AAPL"}]),
}


def client(routes, **kw):
    return IbkrClient(BASE, ACCT, session=FakeSession({**ACCOUNTS, **routes}), sleep=lambda s: None, **kw)


@pytest.mark.parametrize("account", ["U1234567", "", "XDU123", "F123"])
def test_non_paper_account_rejected(account):
    with pytest.raises(ValueError):
        IbkrClient(BASE, account, session=FakeSession({}))


def test_config_account_validation():
    validate_account_id("DU1", "PAPER")
    validate_account_id("", "DRY_RUN")  # offline erlaubt
    with pytest.raises(ValueError):
        validate_account_id("", "PAPER")
    with pytest.raises(ValueError):
        validate_account_id("U123", "DRY_RUN")


def test_ssl_verify_only_disabled_for_localhost():
    assert IbkrClient(BASE, ACCT, session=FakeSession({}))._verify is False
    assert IbkrClient("https://127.0.0.1:5000/v1/api", ACCT, session=FakeSession({}))._verify is False
    assert IbkrClient("https://gateway.example.com/v1/api", ACCT, session=FakeSession({}))._verify is True


@pytest.mark.parametrize(
    "raw, expected",
    [("C213.25", 213.25), ("H99.5", 99.5), ("213.25", 213.25), (213.25, 213.25), ("1,234.50", 1234.5),
     ("", None), (None, None), ("N/A", None), ("C0", None)],
)
def test_snapshot_prefix_parsing(raw, expected):
    assert parse_snapshot_price(raw) == expected


def test_snapshot_retries_once_when_first_call_empty():
    c = client({("GET", "/iserver/marketdata/snapshot"): [ok([{"conid": 265598}]), ok([{"conid": 265598, "31": "C213.25"}])]})
    assert c.get_last_price("AAPL") == 213.25


def test_order_reply_confirmation_loop():
    c = client({
        ("POST", f"/iserver/account/{ACCT}/orders"): ok([{"id": "r1", "message": ["Are you sure?"]}]),
        ("POST", "/iserver/reply/r1"): ok([{"id": "r2", "message": ["Price exceeds 3%"]}]),
        ("POST", "/iserver/reply/r2"): ok([{"order_id": "987", "order_status": "Submitted"}]),
    })
    result = c.place_limit_order("AAPL", "BUY", 1, 196.004)
    assert result.order_id == "987" and result.status == "Submitted"
    assert result.messages == ["Are you sure?", "Price exceeds 3%"]
    calls = c._session.calls
    order_call = next(k for m, p, k in calls if p.endswith("/orders") and m == "POST")
    assert order_call["json"] == {"orders": [{"conid": 265598, "orderType": "LMT", "price": 196.0, "side": "BUY",
                                              "quantity": 1, "tif": "GTC"}]}
    replies = [k["json"] for m, p, k in calls if p.startswith("/iserver/reply/")]
    assert replies == [{"confirmed": True}, {"confirmed": True}]
    # /iserver/accounts wurde vor der Order aufgerufen
    paths = [p for _, p, _ in calls]
    assert paths.index("/iserver/accounts") < paths.index(f"/iserver/account/{ACCT}/orders")


def test_order_reply_loop_gives_up():
    c = client({
        ("POST", f"/iserver/account/{ACCT}/orders"): ok([{"id": "r", "message": ["?"]}]),
        ("POST", "/iserver/reply/r"): ok([{"id": "r", "message": ["?"]}]),
    })
    with pytest.raises(BrokerError):
        c.place_market_order("AAPL", "BUY", 1)
    assert sum(1 for _, p, _ in c._session.calls if p == "/iserver/reply/r") == MAX_REPLY_CONFIRMATIONS


def test_market_order_body_and_error():
    c = client({("POST", f"/iserver/account/{ACCT}/orders"): ok({"error": "No trading permissions"})})
    with pytest.raises(BrokerError, match="No trading permissions"):
        c.place_market_order("AAPL", "BUY", 1)
    body = next(k["json"] for m, p, k in c._session.calls if p.endswith("/orders"))
    assert body["orders"][0] | {} == {"conid": 265598, "orderType": "MKT", "side": "BUY", "quantity": 1, "tif": "DAY"}


def test_account_must_be_in_gateway_login():
    c = IbkrClient(BASE, ACCT, session=FakeSession({
        ("GET", "/portfolio/accounts"): ok([{"id": "DU9999999"}]),
    }))
    with pytest.raises(BrokerError, match="Paper-Login"):
        c.get_positions()


def test_positions_cash_and_orders_parsing():
    c = client({
        ("GET", f"/portfolio/{ACCT}/positions/0"): ok([
            {"conid": 265598, "contractDesc": "AAPL", "ticker": "AAPL", "position": 2.0, "avgPrice": 201.5,
             "avgCost": 201.5, "mktPrice": 205.0, "unrealizedPnl": 7.0},
            {"conid": 1, "contractDesc": "OLD", "position": 0},
        ]),
        ("GET", f"/portfolio/{ACCT}/ledger"): ok({"BASE": {"cashbalance": 998765.4}}),
        ("GET", "/iserver/account/orders"): [
            ok({"orders": [], "snapshot": False}),
            ok({"orders": [
                {"acct": ACCT, "orderId": 11, "ticker": "AAPL", "conid": 265598, "side": "SELL", "totalSize": 1.0,
                 "price": "210.5", "status": "Submitted", "orderType": "Limit"},
                {"acct": ACCT, "orderId": 12, "ticker": "AAPL", "side": "BUY", "totalSize": 1.0,
                 "status": "Filled", "orderType": "Market", "price": "", "avgPrice": "331.21"},
                {"acct": "DU0000001", "orderId": 13, "ticker": "X", "side": "BUY", "status": "Submitted"},
            ]}),
        ],
    })
    [pos] = c.get_positions()
    assert (pos.symbol, pos.qty, pos.avg_price, pos.market_price) == ("AAPL", 2.0, 201.5, 205.0)
    assert c.get_cash() == 998765.4
    orders = c.get_orders()  # erster Aufruf leer -> zweiter
    assert [(o.order_id, o.side, o.order_type, o.limit_price, o.is_open) for o in orders] == [
        ("11", "SELL", "LMT", 210.5, True),
        ("12", "BUY", "MKT", None, False),
    ]
    assert [o.avg_fill_price for o in orders] == [None, 331.21]
    assert [o.order_id for o in c.get_open_orders()] == ["11"]


def test_auth_status_and_errors():
    c = client({("POST", "/iserver/auth/status"): ok({"authenticated": False, "connected": True, "competing": True})})
    assert c.auth_status() == {"authenticated": False, "connected": True, "competing": True, "message": ""}
    c = client({("POST", "/iserver/auth/status"): FakeResponse(401, {"error": "not authenticated"})})
    with pytest.raises(NotAuthenticatedError):
        c.auth_status()
    c = client({("POST", "/tickle"): requests.ConnectionError("refused")})
    with pytest.raises(BrokerError, match="Gateway"):
        c.keepalive()


def test_cancel_order():
    c = client({("DELETE", f"/iserver/account/{ACCT}/order/55"): ok({"msg": "Request was submitted"})})
    c.cancel_order("55")
    assert c._session.calls[-1][:2] == ("DELETE", f"/iserver/account/{ACCT}/order/55")
