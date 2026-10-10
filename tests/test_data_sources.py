import json
import sys

import requests

from quantdesk.broker.base import BrokerError
from quantdesk.marketdata import MarketData, parse_stooq_csv, parse_yahoo_chart, stooq_symbol, yahoo_symbol
from quantdesk.news import parse_rss

STOOQ_CSV = """Date,Open,High,Low,Close,Volume
2026-09-25,210,214,209,213.25,1000
2026-09-26,213,215,212,214.10,1200
"""

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Yahoo</title>
<item><title>Apple unveils new product</title><pubDate>Mon, 28 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>  </title></item>
<item><title>Apple stock rises</title></item>
</channel></rss>"""


YAHOO = {"chart": {"result": [{"meta": {"symbol": "AAPL", "regularMarketPrice": 332.09}}]}}


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
        self.text = body if isinstance(body, str) else json.dumps(body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return json.loads(self.text)


class Session:
    """Antworten pro Host: 'stooq' / 'yahoo' -> Resp."""

    def __init__(self, stooq=None, yahoo=None):
        self.responses = {"stooq": stooq, "yahoo": yahoo}
        self.urls = []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        resp = self.responses["stooq" if "stooq" in url else "yahoo"]
        if resp is None:
            raise requests.ConnectionError("offline")
        return resp


class NoPriceBroker:
    def __init__(self):
        self.calls = 0

    def get_last_price(self, symbol):
        self.calls += 1
        raise BrokerError("kein Abo")


def test_symbol_formats():
    assert stooq_symbol("AAPL") == "aapl.us"
    assert stooq_symbol("aapl.us") == "aapl.us"
    assert stooq_symbol("BRK.B") == "brk-b.us"
    assert yahoo_symbol("brk.b") == "BRK-B"


def test_parsers():
    assert parse_stooq_csv(STOOQ_CSV) == 214.10
    assert parse_stooq_csv("No data") is None
    assert parse_stooq_csv("<html>JavaScript check</html>") is None
    assert parse_yahoo_chart(YAHOO) == 332.09
    assert parse_yahoo_chart({"chart": {"result": None}}) is None


def test_ibkr_then_stooq_with_cache():
    t = [0.0]
    session = Session(stooq=Resp(STOOQ_CSV), yahoo=Resp(YAHOO))
    broker = NoPriceBroker()
    md = MarketData(broker, session=session, clock=lambda: t[0])
    q = md.get_quote("aapl")
    assert (q.price, q.source) == (214.10, "Stooq")
    assert session.urls == ["https://stooq.com/q/d/l/?s=aapl.us&i=d"]
    md.get_quote("AAPL")
    assert len(session.urls) == 1 and broker.calls == 1  # Stooq gecached, IBKR-Fehlschlag gemerkt
    t[0] += 1000
    md.get_quote("AAPL")
    assert len(session.urls) == 2 and broker.calls == 2


def test_stooq_blocked_falls_back_to_yahoo():
    t = [0.0]
    session = Session(stooq=Resp("<html>verify browser</html>", 404), yahoo=Resp(YAHOO))
    md = MarketData(None, session=session, clock=lambda: t[0])
    q = md.get_quote("AAPL")
    assert (q.price, q.source) == (332.09, "Yahoo")
    md.get_quote("AAPL")
    assert len(session.urls) == 2  # Stooq-Fehlschlag + Yahoo, danach beides aus dem Cache


def test_no_source_available():
    assert MarketData(NoPriceBroker(), session=Session()).get_quote("AAPL") is None


def test_ibkr_price_preferred():
    class Broker:
        def get_last_price(self, symbol):
            return 99.0

    q = MarketData(Broker(), session=Session()).get_quote("AAPL")
    assert (q.price, q.source) == (99.0, "IBKR")


def test_parse_rss():
    items = parse_rss(RSS, limit=5)
    assert [i["title"] for i in items] == ["Apple unveils new product", "Apple stock rises"]
    assert parse_rss("kein xml") == []
    assert len(parse_rss(RSS, limit=1)) == 1


def test_core_modules_do_not_import_tkinter():
    import quantdesk.app  # noqa: F401

    assert "tkinter" not in sys.modules


# ---------------------------------------------------------------- Erweiterung für Lotse (SIX): Mapping, Datum, Währung
import datetime as dt  # noqa: E402

YAHOO_SIX = {"chart": {"result": [{"meta": {"symbol": "SSAC.SW", "regularMarketPrice": 102.94, "currency": "CHF",
                                            "regularMarketTime": 1791560185, "gmtoffset": 7200}}]}}


def test_bot_c_unveraendert_ohne_mapping():
    session = Session(stooq=Resp(STOOQ_CSV))
    q = MarketData(None, session=session).get_quote("AAPL")
    assert (q.symbol, q.price, q.source) == ("AAPL", 214.10, "Stooq")
    assert session.urls == ["https://stooq.com/q/d/l/?s=aapl.us&i=d"]
    assert q.datum == dt.date(2026, 9, 26) and q.waehrung is None  # Stooq nennt keine Währung
    assert stooq_symbol("SSAC") == "ssac.us" and yahoo_symbol("SSAC.SW") == "SSAC-SW"  # Standard bleibt


def test_mapping_je_quelle_fuer_die_six():
    session = Session(stooq=Resp(STOOQ_CSV), yahoo=Resp(YAHOO_SIX))
    md = MarketData(None, session=session, symbole={"SSAC": {"yahoo": "SSAC.SW", "stooq": ""}})
    q = md.get_quote("ssac")
    assert session.urls == ["https://query1.finance.yahoo.com/v8/finance/chart/SSAC.SW?range=5d&interval=1d"]
    assert (q.price, q.source, q.waehrung, q.datum) == (102.94, "Yahoo", "CHF", dt.date(2026, 10, 9))


def test_mapping_stooq_symbol_fuer_ein_anderes_land():
    session = Session(stooq=Resp(STOOQ_CSV))
    MarketData(None, session=session, symbole={"SSAC": {"stooq": "ssac.ch"}}).get_quote("SSAC")
    assert session.urls == ["https://stooq.com/q/d/l/?s=ssac.ch&i=d"]
