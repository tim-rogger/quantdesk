"""Test-Doubles für Lotse: ein Nachbau der benutzten ib_async-Schnittstelle (kein Netzwerk, kein Gateway)."""
import datetime as dt
from types import SimpleNamespace as NS

UNSET_DOUBLE = 1.7976931348623157e308  # = ib_async.util.UNSET_DOUBLE


class FakeIB:
    def __init__(self, konto="DUO844164"):
        self.konten = [konto]
        self.verbunden = False
        self.cash = 10_000.0
        self.pos = {}  # Symbol -> Stück
        self.preise = {}  # Symbol -> (last, close, zeit)
        self.offen = []  # Trades
        self.fills = []
        self.gesendet = []
        self.storniert = []
        self.ablehnen = False
        self.tick = 0.01
        self._perm = 5000

    def isConnected(self):
        return self.verbunden

    def connect(self, host, port, clientId, timeout):
        self.verbunden = True

    def disconnect(self):
        self.verbunden = False

    def managedAccounts(self):
        return self.konten

    def reqMarketDataType(self, typ):
        pass

    def sleep(self, s=0):
        pass

    def qualifyContracts(self, vertrag):
        vertrag.conId = 100 + len(vertrag.symbol)
        return [vertrag]

    def reqContractDetails(self, vertrag):
        return [NS(minTick=self.tick)]

    def accountValues(self, konto=""):
        return [NS(tag="TotalCashValue", currency="CHF", value=str(self.cash)),
                NS(tag="TotalCashValue", currency="USD", value="123")]

    def positions(self, konto=""):
        return [NS(contract=NS(symbol=s), position=q) for s, q in self.pos.items()]

    def reqTickers(self, vertrag):
        last, close, zeit = self.preise.get(vertrag.symbol, (float("nan"), float("nan"), None))
        return [NS(last=last, close=close, time=zeit)]

    def reqAllOpenOrders(self):
        return list(self.offen)

    def reqExecutions(self, filt=None):
        return list(self.fills)

    def placeOrder(self, vertrag, order):
        self.gesendet.append((vertrag.symbol, order))
        if self.ablehnen:
            return NS(order=order, orderStatus=NS(status="Inactive"), log=[NS(message="Order abgelehnt (Test)")])
        self._perm += 1
        order.permId = self._perm
        trade = NS(contract=NS(symbol=vertrag.symbol), order=order, orderStatus=NS(status="Submitted"), log=[])
        self.offen.append(trade)
        return trade

    def cancelOrder(self, order):
        self.storniert.append(order.orderRef)
        self.offen = [t for t in self.offen if t.order.permId != order.permId]

    # Test-Helfer
    def offene_order(self, ref, symbol="VWRL", aktion="BUY", menge=1.0, perm=777, konto="DUO844164"):
        order = NS(orderRef=ref, permId=perm, action=aktion, totalQuantity=menge, account=konto,
                   filledQuantity=UNSET_DOUBLE)  # so liefert ib_async offene Orders
        self.offen.append(NS(contract=NS(symbol=symbol), order=order, orderStatus=NS(status="Submitted")))

    def ausfuehrung(self, ref, exec_id, symbol="VWRL", seite="BOT", menge=1.0, preis=155.0):
        self.fills.append(NS(contract=NS(symbol=symbol),
                             execution=NS(execId=exec_id, orderRef=ref, side=seite, shares=menge, price=preis,
                                          time=dt.datetime(2026, 10, 9, 10, 0))))
