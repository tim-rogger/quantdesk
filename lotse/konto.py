"""Lotse – Verbindung zu IBKR (IB Gateway, TWS-API über ib_async): Cash, Positionen, offene Orders, Kurse,
Orders senden und stornieren. Nur Paper-Konten (DU…). Kein Code von Bot C – eigene, kleine Umsetzung.

Wichtig (Lehre aus Bot C): ib_async füllt fehlende Zahlen mit Platzhaltern (UNSET_DOUBLE = 1.8e308,
UNSET_INTEGER = 2147483647). Solche Werte gelten hier als "nicht vorhanden" (None) und werden nie
weitergegeben. Ausgeführte Mengen kommen nur aus den Ausführungen (Executions), nie aus der Order.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from lotse.logik import KAUF, VERKAUF, Kurs
from lotse.tagebuch import REF_PREFIX

PAPER_PREFIX = "DU"
PLATZHALTER_AB = 1e300  # ab hier ist eine Zahl ein ib_async-Platzhalter
GUELTIGKEIT = "DAY"  # Regel 20: nur bis Handelsschluss, nie GTC


class KontoFehler(Exception):
    pass


def echte_zahl(wert) -> float | None:
    """Zahl vom Broker übernehmen – Platzhalter und Unlesbares werden zu None. nan/inf/0/negativ bleiben
    erhalten, damit Regel 0 sie sieht (bei Kursen heisst nan "kein Kurs" und wird None, siehe kurse)."""
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return None
    if math.isfinite(zahl) and abs(zahl) >= PLATZHALTER_AB:
        return None
    if abs(zahl) == 2147483647:
        return None
    return zahl


@dataclass(frozen=True)
class OffeneOrder:
    ref: str
    perm_id: int
    papier: str
    seite: str
    stueck: float


@dataclass(frozen=True)
class Ausfuehrung:
    exec_id: str
    ref: str
    papier: str
    seite: str
    menge: float | None
    preis: float | None


def _kurs_oder_none(wert) -> float | None:
    """ib_async meldet einen fehlenden Kurs als nan – das heisst hier "kein Kurs" (None)."""
    zahl = echte_zahl(wert)
    return None if zahl is None or math.isnan(zahl) else zahl


def auf_tick(limit: float, tick: float | None, seite: str) -> float:
    """Limit auf den Preisschritt der Börse bringen, den die Börse annimmt. Nie ungünstiger als Tims Limit:
    beim Kauf abrunden (nie mehr zahlen), beim Verkauf aufrunden (nie billiger verkaufen)."""
    if not tick:
        return limit
    schritte = limit / tick
    schritte = math.floor(schritte + 1e-9) if seite == KAUF else math.ceil(schritte - 1e-9)
    return round(schritte * tick, 10)


def _seite(action: str) -> str:
    return KAUF if action.upper() in ("BUY", "BOT") else VERKAUF


class Konto:
    def __init__(self, host: str, port: int, client_id: int, konto: str, boerse: str, waehrung: str,
                 ib=None, wartezeit: float = 3.0):
        if not konto.upper().startswith(PAPER_PREFIX):
            raise KontoFehler(f"Konto {konto!r} abgelehnt: Lotse läuft nur auf Paper-Konten ({PAPER_PREFIX}…).")
        self.host, self.port, self.client_id = host, port, client_id
        self.konto, self.boerse, self.waehrung = konto.upper(), boerse, waehrung
        self.wartezeit = wartezeit
        if ib is None:
            from ib_async import IB

            ib = IB()
        self.ib = ib
        self._vertraege: dict[str, object] = {}

    # ------------------------------------------------------------------ Verbindung
    def verbinden(self) -> None:
        if self.ib.isConnected():
            return
        try:
            self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=20)
        except (OSError, TimeoutError, ConnectionError) as e:
            raise KontoFehler(f"IB Gateway nicht erreichbar ({self.host}:{self.port}): {e}") from e
        konten = [k.upper() for k in self.ib.managedAccounts()]
        if self.konto not in konten:
            self.ib.disconnect()
            raise KontoFehler(f"Gateway meldet {konten}, erwartet {self.konto} – abgebrochen.")
        self.ib.reqMarketDataType(3)  # verzögerte Kurse sind erlaubt (kein Echtzeit-Abo nötig)

    def trennen(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    def _vertrag(self, papier: str):
        if papier not in self._vertraege:
            from ib_async import Stock

            gefunden = [v for v in self.ib.qualifyContracts(Stock(papier, self.boerse, self.waehrung)) if v]
            if not gefunden or not getattr(gefunden[0], "conId", 0):
                raise KontoFehler(f"{papier}: IBKR kennt das Papier an {self.boerse} in {self.waehrung} nicht.")
            self._vertraege[papier] = gefunden[0]
        return self._vertraege[papier]

    # ------------------------------------------------------------------ Lesen (Regel 2, 3)
    def cash(self) -> float | None:
        """Cash in der Kontowährung (CHF). None, wenn IBKR keinen Wert meldet."""
        for wert in self.ib.accountValues(self.konto):
            if wert.tag == "TotalCashValue" and wert.currency == self.waehrung:
                return echte_zahl(wert.value)
        return None

    def positionen(self) -> dict[str, float]:
        """Stück je Papier – ALLE Positionen im Konto, auch die, die Lotse nichts angehen (Regel 17 entscheidet)."""
        out: dict[str, float] = {}
        for p in self.ib.positions(self.konto):
            menge = echte_zahl(p.position)
            if menge:
                out[p.contract.symbol] = menge
        return out

    def kurse(self, papiere: list[str]) -> dict[str, Kurs]:
        """Letzter Kurs je Papier mit Datum. Fehlt etwas, steht None drin – Regel 0 entscheidet."""
        out = {}
        for papier in papiere:
            try:
                ticker = self.ib.reqTickers(self._vertrag(papier))[0]
            except (KontoFehler, IndexError):
                out[papier] = Kurs(None, None)
                continue
            wert = _kurs_oder_none(ticker.last)
            if wert is None:
                wert = _kurs_oder_none(ticker.close)
            zeit = getattr(ticker, "time", None)
            out[papier] = Kurs(wert, zeit.date() if isinstance(zeit, dt.datetime) else None)
        return out

    def offene_orders(self) -> list[OffeneOrder]:
        """Offene Orders von Lotse (Referenz beginnt mit 'lotse-'). Fremde Orders werden nie angefasst."""
        out = []
        for trade in self.ib.reqAllOpenOrders():
            ref = getattr(trade.order, "orderRef", "") or ""
            if not ref.startswith(REF_PREFIX) or (trade.order.account or "").upper() != self.konto:
                continue
            out.append(OffeneOrder(ref, int(trade.order.permId), trade.contract.symbol, _seite(trade.order.action),
                                   echte_zahl(trade.order.totalQuantity) or 0.0))
        return out

    def ausfuehrungen(self) -> list[Ausfuehrung]:
        """Ausführungen von Lotse-Orders (IBKR liefert die letzten Tage). Die Menge kommt von hier – nie
        aus Order.filledQuantity, das bei offenen Orders den Platzhalter 1.8e308 enthält."""
        from ib_async import ExecutionFilter

        out = []
        for fill in self.ib.reqExecutions(ExecutionFilter(acctCode=self.konto)):
            e = fill.execution
            ref = getattr(e, "orderRef", "") or ""
            if ref.startswith(REF_PREFIX):
                out.append(Ausfuehrung(str(e.execId), ref, fill.contract.symbol, _seite(e.side),
                                       echte_zahl(e.shares), echte_zahl(e.price)))
        return out

    def tick(self, papier: str) -> float | None:
        """Kleinster erlaubter Preisschritt an der Börse (z.B. 0.01 CHF). None, wenn IBKR keinen meldet."""
        details = self.ib.reqContractDetails(self._vertrag(papier))
        tick = echte_zahl(details[0].minTick) if details else None
        return tick if tick and tick > 0 else None

    # ------------------------------------------------------------------ Schreiben (Regel 4, 20)
    def storniere(self, order: OffeneOrder) -> None:
        for trade in self.ib.reqAllOpenOrders():
            if int(trade.order.permId) == order.perm_id and trade.order.orderRef == order.ref:
                self.ib.cancelOrder(trade.order)
                return
        raise KontoFehler(f"Order {order.ref} nicht mehr offen")

    def sende_limit(self, papier: str, seite: str, stueck: float, limit: float, ref: str) -> int:
        """Limit-Order senden, gültig nur bis Handelsschluss (DAY, nie GTC), nur während der Handelszeit.
        Liefert die permId. Lehnt IBKR ab, gibt es einen KontoFehler (IBKR-Warnungen werden nie bestätigt)."""
        from ib_async import LimitOrder

        if not (stueck > 0 and math.isfinite(stueck) and limit > 0 and math.isfinite(limit)):
            raise KontoFehler(f"{ref}: ungültige Order ({stueck} Stück zu {limit})")
        limit = auf_tick(limit, self.tick(papier), seite)
        order = LimitOrder("BUY" if seite == KAUF else "SELL", stueck, limit, tif=GUELTIGKEIT,
                           account=self.konto, orderRef=ref, outsideRth=False)
        assert order.tif == GUELTIGKEIT  # Regel 20: nie GTC
        trade = self.ib.placeOrder(self._vertrag(papier), order)
        self.ib.sleep(self.wartezeit)
        status = trade.orderStatus.status
        if status in ("Cancelled", "ApiCancelled", "Inactive") or not trade.order.permId:
            gruende = "; ".join(str(getattr(e, "message", e)) for e in getattr(trade, "log", [])[-3:])
            raise KontoFehler(f"{ref}: IBKR hat die Order nicht angenommen ({status}). {gruende}")
        return int(trade.order.permId)
