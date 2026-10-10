"""Lotse – alle Regeln. Das ist Tims Datei (die Umsetzung hat Claude am 10.10.2026 auf Tims Wunsch geschrieben).

Hier steht nur Rechnen und Entscheiden: Zahlen rein, Zahlen raus. Kein Broker, kein Internet, keine Uhr.
Was eine Regel von aussen braucht (Kurse, Datum, Tagebuch), bekommt sie als Argument.
Importiert wird nur die Standardbibliothek.

Die Regeln stehen im Auftrag (AUFTRAG_LOTSE_1, Abschnitt 4). Jede Funktion sagt, welche Regel sie ist.
Zu jeder Regel gibt es Tests mit konkreten Zahlen in tests/test_logik.py.

Ein Wertpapier heisst hier immer mit seinem Börsenkürzel, z.B. "VWRL". Die Reihenfolge in `ziel` ist die
Reihenfolge aus config.toml: das erste ist "Wertpapier 1".
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

KAUF = "KAUF"
VERKAUF = "VERKAUF"


# --------------------------------------------------------------------------- Datentypen (nur Behälter)
@dataclass(frozen=True)
class Kurs:
    """Letzter Kurs eines Wertpapiers in CHF und der Tag, von dem er stammt. Beides kann fehlen (None)."""
    wert: float | None
    datum: dt.date | None


@dataclass(frozen=True)
class Grenzen:
    """Die Zahlen aus [grenzen], [gebuehren] und [ausfuehren] in config.toml."""
    budget_chf: float
    max_einzahlung_pro_lauf: float
    mindestbetrag: float
    schwelle: float
    mindestdepot: float
    puffer_prozent: float
    max_pro_order: float
    max_pro_tag: float
    max_orders_pro_tag: int
    gebuehr_pro_order: float
    limit_abstand_prozent: float
    bruchstueck_stellen: int


@dataclass(frozen=True)
class Einstellungen:
    """Was Lotse erreichen soll: Zielanteile in Prozent je Wertpapier, die Abbau-Liste und die Grenzen."""
    ziel: dict[str, float]
    abbau: tuple[str, ...]
    grenzen: Grenzen


@dataclass(frozen=True)
class Lage:
    """Alles, was der Lauf vor dem Rechnen gelesen hat.

    cash                CHF auf dem Konto (None = nicht lesbar)
    stueck              Stück je Wertpapier laut Broker – auch Papiere, die Lotse nichts angehen
    eigene_stueck       Stück je Wertpapier, die Lotse selbst gekauft hat (Tagebuch, Regel 15)
    investiert          CHF, die Lotse netto verbraucht hat: Käufe + Kommission − Verkaufserlöse nach Kommission
    kurse               Kurs je Wertpapier aus Ziel- und Abbau-Liste
    letzter_handelstag  der letzte abgeschlossene Handelstag der SIX (für "Kurs zu alt")
    mindestdepot_erreicht  im Tagebuch vermerkt: das Depot hat das Mindestdepot schon einmal erreicht (Regel 12)
    cash_letzter_lauf   Cash beim letzten erfolgreichen Lauf (None = noch keiner) – für die Einzahlungsprüfung
    investiert_letzter_lauf  "investiert" beim letzten erfolgreichen Lauf
    einzahlung_bestaetigt  Tim hat eine grosse Einzahlung für diesen Lauf bestätigt
    heute_betrag        heute schon bestellt, in CHF
    heute_orders        heute schon bestellt, Anzahl Orders
    """
    cash: float | None
    stueck: dict[str, float]
    eigene_stueck: dict[str, float]
    kurse: dict[str, Kurs]
    letzter_handelstag: dt.date
    mindestdepot_erreicht: bool = False
    cash_letzter_lauf: float | None = None
    investiert_letzter_lauf: float = 0.0
    einzahlung_bestaetigt: bool = False
    heute_betrag: float = 0.0
    heute_orders: int = 0
    investiert: float = 0.0


@dataclass(frozen=True)
class Order:
    """Eine geplante Order: welches Papier, KAUF oder VERKAUF, für wie viele CHF."""
    papier: str
    seite: str
    betrag: float


@dataclass(frozen=True)
class Abbruch:
    """Regel 0: Lotse macht nichts. `grund` sagt in einem Satz, warum (kommt so in den Push)."""
    grund: str


@dataclass(frozen=True)
class Plan:
    """Ergebnis eines Laufs: entweder Orders (auch leer = nichts zu tun) oder ein Abbruch."""
    orders: list[Order] = field(default_factory=list)
    abbruch: Abbruch | None = None


# --------------------------------------------------------------------------- Regel 0 – Grundhaltung
def regel_0_pruefe_lage(lage: Lage, einstellungen: Einstellungen) -> Abbruch | None:
    """Regel 0: Ist alles da und plausibel?

    Rein:   die gelesene Lage und die Einstellungen.
    Raus:   None, wenn alles in Ordnung ist. Sonst ein Abbruch mit dem Grund als Text.
    Abbruch, wenn: Cash fehlt oder negativ ist; für ein Papier aus Ziel- oder Abbau-Liste der Kurs fehlt,
    0, negativ, nan oder inf ist, oder älter als `letzter_handelstag`.
    Der Grund nennt das betroffene Papier.
    """
    if lage.cash is None or not math.isfinite(lage.cash) or lage.cash < 0:
        return Abbruch(f"Cash nicht lesbar oder negativ ({lage.cash})")
    for papier in _papiere(einstellungen):
        kurs = lage.kurse.get(papier)
        if kurs is None or kurs.wert is None:
            return Abbruch(f"Kurs von {papier} fehlt")
        if not math.isfinite(kurs.wert) or kurs.wert <= 0:
            return Abbruch(f"Kurs von {papier} ist unplausibel ({kurs.wert})")
        if kurs.datum is None or kurs.datum < lage.letzter_handelstag:
            return Abbruch(f"Kurs von {papier} ist zu alt ({kurs.datum})")
    return regel_0_einzahlung_plausibel(lage, einstellungen.grenzen)


def regel_0_einzahlung_plausibel(lage: Lage, grenzen: Grenzen) -> Abbruch | None:
    """Regel 0 (Einzahlung): Ohne Budget (budget_chf = 0) ist eine ungewöhnlich grosse Einzahlung verdächtig.

    Einzahlung = Cash-Zuwachs seit dem letzten Lauf + Zuwachs von "investiert". Was Lotse selbst bewegt hat
    (Käufe, Verkäufe, Gebühren, Ausschüttungen), hebt sich dabei auf. Ohne früheren Lauf zählt das ganze Cash.
    Raus:   Abbruch, wenn die Einzahlung grösser als max_einzahlung_pro_lauf ist und Tim sie nicht bestätigt hat.
            Mit Budget (budget_chf > 0) gibt es diese Prüfung nicht – dort begrenzt das Budget.
    """
    if grenzen.budget_chf != 0 or lage.einzahlung_bestaetigt:
        return None
    vorher = lage.cash_letzter_lauf if lage.cash_letzter_lauf is not None else 0.0
    einzahlung = (lage.cash - vorher) + (lage.investiert - lage.investiert_letzter_lauf)
    if einzahlung > grenzen.max_einzahlung_pro_lauf:
        return Abbruch(f"Ungewöhnlich grosse Einzahlung: {einzahlung:.2f} CHF seit dem letzten Lauf "
                       f"(Grenze {grenzen.max_einzahlung_pro_lauf:.2f} CHF). Bitte prüfen und bestätigen: "
                       "python -m lotse.lauf --einzahlung-bestaetigt")
    return None


# --------------------------------------------------------------------------- Block 2 – Rechnen
def regel_5_werte(stueck: dict[str, float], kurse: dict[str, Kurs]) -> dict[str, float]:
    """Regel 5a: Wert jeder Position in CHF = Stück × Kurs.

    Rein:   Stück je Papier (die EIGENEN, aus dem Tagebuch – nicht die vom Konto) und Kurse je Papier.
    Raus:   Wert je Papier in CHF – nur für Papiere, für die es einen Kurs gibt.
    """
    werte = {}
    for papier, anzahl in stueck.items():
        kurs = kurse.get(papier)
        if kurs is not None and kurs.wert is not None:
            werte[papier] = anzahl * kurs.wert
    return werte


def regel_5_gesamt(werte: dict[str, float], cash: float, budget: float, investiert: float) -> float:
    """Regel 5b: Gesamtwert des Lotse-Depots in CHF = Summe der eigenen Werte + Lotse-Geld.

    Lotse-Geld siehe `lotse_geld` (mit Budget nur das Restbudget, ohne Budget das ganze Cash).
    """
    return sum(werte.values()) + lotse_geld(cash, budget, investiert)


def lotse_geld(cash: float, budget: float, investiert: float) -> float:
    """Regel 5/7: Wie viel vom Cash gehört Lotse?

    budget = 0: kein Budget – das ganze Cash (Lotse ist allein auf dem Konto).
    budget > 0: das Kleinere von Cash und (Budget − investiert), nie negativ – so zählt das Geld von Bot C
    auf demselben Paper-Konto nicht mit.
    """
    if budget == 0:
        return max(cash, 0.0)
    return max(min(cash, budget - investiert), 0.0)


def regel_6_abweichungen(werte: dict[str, float], gesamt: float, ziel: dict[str, float]) -> dict[str, float]:
    """Regel 6: Wie weit liegt jedes Zielpapier neben seinem Ziel?

    Rein:   Werte je Papier (CHF), Gesamtwert (CHF), Zielanteile (Prozent).
    Raus:   je Zielpapier: Ist-Anteil minus Ziel-Anteil, in Prozentpunkten vom Gesamtdepot.
            Negativ = zu wenig da (Unterdeckung), positiv = zu viel.
    Beispiel: Ziel 15 %, Ist 10 % → −5.
    """
    abweichungen = {}
    for papier, ziel_prozent in ziel.items():
        ist_prozent = werte.get(papier, 0.0) / gesamt * 100 if gesamt > 0 else 0.0
        abweichungen[papier] = ist_prozent - ziel_prozent
    return abweichungen


def regel_7_verfuegbar(cash: float, budget: float, investiert: float, gebuehr: float,
                       puffer_prozent: float) -> float:
    """Regel 7: Wie viel CHF darf Lotse ausgeben?

    Rein:   Cash (CHF), Budget (CHF), bisher investiert (CHF), geschätzte Gebühr einer Order (CHF),
            Puffer in Prozent.
    Raus:   Grundbetrag = Lotse-Geld (`lotse_geld`): mit Budget das Kleinere von Cash und (Budget − investiert),
            ohne Budget (budget = 0) das ganze Cash.
            Verfügbar = Grundbetrag minus Gebühr minus Puffer (Prozent vom Grundbetrag). Nie negativ.
    Das Budget trennt Lotse vom Geld von Bot C auf demselben Paper-Konto.
    """
    grundbetrag = lotse_geld(cash, budget, investiert)
    puffer = grundbetrag * puffer_prozent / 100
    return max(grundbetrag - gebuehr - puffer, 0.0)


# --------------------------------------------------------------------------- Block 3 – Entscheiden
def regel_8_unterdecktestes(abweichungen: dict[str, float], kaufbar: list[str]) -> str | None:
    """Regel 8: Welches Papier bekommt das freie Geld zuerst?

    Rein:   Abweichungen je Papier (Prozentpunkte, Regel 6) und die Papiere, die gekauft werden dürfen.
    Raus:   das kaufbare Papier mit der grössten Unterdeckung (am weitesten im Minus).
            None, wenn kein kaufbares Papier im Minus ist.
    """
    im_minus = [papier for papier in kaufbar if abweichungen.get(papier, 0.0) < 0]
    if not im_minus:
        return None
    return min(im_minus, key=lambda papier: abweichungen[papier])


def regel_9_kaufbetrag(verfuegbar: float, luecke: float, mindestbetrag: float, max_pro_order: float) -> float:
    """Regel 9: Für wie viel CHF wird gekauft?

    Rein:   verfügbares Geld (Regel 7), Lücke des Papiers in CHF (wie viel bis zum Ziel fehlt), Mindestbetrag,
            Höchstbetrag pro Order.
    Raus:   Betrag in CHF; 0 heisst: nicht kaufen.
    Ist die Lücke kleiner als der Mindestbetrag, wird das ganze verfügbare Geld genommen, sonst die Lücke.
    Der Betrag ist nie grösser als das verfügbare Geld und wird auf max_pro_order gekappt.
    Liegt der Orderbetrag unter dem Mindestbetrag, wird nicht gekauft.
    """
    if luecke < mindestbetrag:
        betrag = verfuegbar
    else:
        betrag = min(luecke, verfuegbar)
    betrag = _auf_rappen(min(betrag, max_pro_order))
    if betrag < mindestbetrag:
        return 0.0
    return betrag


def regel_10_kaufbar(gesamt: float, mindestdepot: float, ziel: dict[str, float]) -> list[str]:
    """Regel 10: Welche Zielpapiere dürfen bei dieser Depotgrösse gekauft werden?

    Rein:   Gesamtwert (CHF), Mindestdepot (CHF), Zielanteile (Reihenfolge wie in config.toml).
    Raus:   unter dem Mindestdepot nur Wertpapier 1, sonst alle Zielpapiere.
    """
    papiere = list(ziel)
    if gesamt < mindestdepot:
        return papiere[:1]
    return papiere


def regel_11_umschichten(gesamt: float, abweichung: float, mindestdepot: float, schwelle: float) -> bool:
    """Regel 11: Darf ein zu grosses Papier verkauft werden, um umzuschichten?

    Rein:   Gesamtwert (CHF), Abweichung dieses Papiers (Prozentpunkte, positiv = zu viel), Mindestdepot, Schwelle.
    Raus:   True nur, wenn das Depot das Mindestdepot erreicht hat (≥) UND die Abweichung grösser als die
            Schwelle ist.
    """
    return gesamt >= mindestdepot and abweichung > schwelle


def regel_12_erstmals_ueber_mindestdepot(gesamt: float, schon_erreicht: bool, mindestdepot: float) -> bool:
    """Regel 12: Hat das Depot das Mindestdepot gerade zum ersten Mal erreicht?

    Rein:   Gesamtwert jetzt, ob im Tagebuch schon vermerkt ist, dass es einmal erreicht wurde, Mindestdepot.
    Raus:   True, wenn es jetzt erreicht ist (≥) und noch nie vermerkt wurde.
            Dann wird in diesem Lauf nicht umgeschichtet – Einzahlungen füllen den zweiten Teil auf.
            Der Vermerk wird einmal gesetzt (lauf.py) und nie neu errechnet.
    """
    return gesamt >= mindestdepot and not schon_erreicht


def regel_13_darf_kaufen(papier: str, abbau: tuple[str, ...]) -> bool:
    """Regel 13: Papiere auf der Abbau-Liste werden nur verkauft, nie gekauft. Raus: False, wenn auf der Liste."""
    return papier not in abbau


# --------------------------------------------------------------------------- Block 4 – Absichern
def regel_14_max_verkaufbar(stueck_broker: float, stueck_eigen: float) -> float:
    """Regel 14: Nie mehr Stück verkaufen, als Lotse selbst gekauft hat.

    Rein:   Stück laut Broker und Stück laut eigenem Zähler (Regel 15).
    Raus:   höchstens so viele Stück dürfen verkauft werden.
    """
    return max(min(stueck_broker, stueck_eigen), 0.0)


def regel_16_kauf_gedeckt(betrag: float, verfuegbar: float) -> bool:
    """Regel 16: Nie für mehr kaufen als verfügbar. Raus: True, wenn der Betrag gedeckt ist."""
    return betrag <= verfuegbar + 1e-9


def regel_17_darf_anfassen(papier: str, ziel: dict[str, float], abbau: tuple[str, ...]) -> bool:
    """Regel 17: Lotse fasst nur Papiere an, die in der Ziel- oder Abbau-Liste stehen. Raus: True/False."""
    return papier in ziel or papier in abbau


def regel_18_grenzen(order: Order, heute_betrag: float, heute_orders: int, grenzen: Grenzen) -> Abbruch | None:
    """Regel 18: Hält diese Order die Tagesgrenzen ein? (Nur noch Nachprüfung – Regel 9 kappt schon vorher.)

    Rein:   die geplante Order, heute schon bestellter Betrag (CHF) und Anzahl Orders, die Grenzen.
    Raus:   None, wenn alles passt. Abbruch mit Grund, wenn die Order über max_pro_order liegt, der Tag damit
            über max_pro_tag käme oder es mehr als max_orders_pro_tag Orders würden.
    """
    if order.betrag > grenzen.max_pro_order + 1e-9:
        return Abbruch(f"Order {order.papier} über {order.betrag:.2f} CHF ist grösser als max_pro_order")
    if heute_betrag + order.betrag > grenzen.max_pro_tag + 1e-9:
        return Abbruch(f"Mit {order.papier} wären heute {heute_betrag + order.betrag:.2f} CHF bestellt – über max_pro_tag")
    if heute_orders + 1 > grenzen.max_orders_pro_tag:
        return Abbruch(f"Das wäre heute Order Nr. {heute_orders + 1} – mehr als max_orders_pro_tag")
    return None


# --------------------------------------------------------------------------- Regel 20 – Rechenteil
def regel_20_stueck_und_limit(order: Order, kurs: float, limit_abstand_prozent: float,
                              bruchstueck_stellen: int) -> tuple[float, float]:
    """Regel 20 (Rechenteil): Aus einem Betrag in CHF wird eine Limit-Order in Stück.

    Rein:   die Order (Betrag in CHF), der letzte Kurs, wie viel Prozent das Limit vom Kurs entfernt ist,
            wie viele Nachkommastellen Stück haben dürfen (0 = nur ganze Stück).
    Raus:   (Stück, Limitpreis).
            Kauf: Limit über dem Kurs, Verkauf: Limit unter dem Kurs.
            Stück abgerundet auf die erlaubten Nachkommastellen, so dass Stück × Limit nie über dem Betrag liegt.
    (Senden, "nur bis Handelsschluss" und "nie GTC" erledigt das Gerüst in konto.py.)
    """
    if order.seite == KAUF:
        limit = kurs * (1 + limit_abstand_prozent / 100)
    else:
        limit = kurs * (1 - limit_abstand_prozent / 100)
    faktor = 10 ** bruchstueck_stellen
    stueck = math.floor(order.betrag / limit * faktor) / faktor
    return stueck, limit


# --------------------------------------------------------------------------- alles zusammen
def plane(lage: Lage, einstellungen: Einstellungen) -> Plan:
    """Der ganze Entscheid eines Laufs: verbindet Regel 0, 5–14, 16–18.

    Rein:   die gelesene Lage und die Einstellungen.
    Raus:   ein Plan – Orders (leer = nichts zu tun) oder ein Abbruch (Regel 0) mit Grund.
    Reihenfolge wie im Auftrag: prüfen (0) → rechnen (5–7) → entscheiden (8–13) → absichern (14, 16–18).
    """
    abbruch = regel_0_pruefe_lage(lage, einstellungen)
    if abbruch is not None:
        return Plan(abbruch=abbruch)
    g = einstellungen.grenzen
    werte = regel_5_werte(lage.eigene_stueck, lage.kurse)
    gesamt = regel_5_gesamt(werte, lage.cash, g.budget_chf, lage.investiert)
    abweichungen = regel_6_abweichungen(werte, gesamt, _ziel_mit_abbau(einstellungen))
    verfuegbar = regel_7_verfuegbar(lage.cash, g.budget_chf, lage.investiert, g.gebuehr_pro_order, g.puffer_prozent)

    orders = _verkaeufe(lage, einstellungen, werte, gesamt, abweichungen)
    kauf = _kauf(einstellungen, gesamt, abweichungen, verfuegbar)
    if kauf is not None:
        orders.append(kauf)

    heute_betrag, heute_orders = lage.heute_betrag, lage.heute_orders
    for order in orders:
        abbruch = regel_18_grenzen(order, heute_betrag, heute_orders, g)
        if abbruch is not None:
            return Plan(abbruch=abbruch)
        heute_betrag += order.betrag
        heute_orders += 1
    return Plan(orders=orders)


# --------------------------------------------------------------------------- Hilfsfunktionen für plane
def _auf_rappen(betrag: float) -> float:
    """CHF-Betrag auf Rappen abrunden (nie mehr als gerechnet)."""
    return math.floor(betrag * 100 + 1e-6) / 100


def _papiere(einstellungen: Einstellungen) -> list[str]:
    """Alle Papiere, die Lotse etwas angehen: Zielpapiere, dann die übrigen der Abbau-Liste."""
    return list(einstellungen.ziel) + [p for p in einstellungen.abbau if p not in einstellungen.ziel]


def _ziel_mit_abbau(einstellungen: Einstellungen) -> dict[str, float]:
    """Ziel je Papier; Papiere nur auf der Abbau-Liste haben das Ziel 0 %."""
    return {papier: einstellungen.ziel.get(papier, 0.0) for papier in _papiere(einstellungen)}


def _verkaeufe(lage: Lage, einstellungen: Einstellungen, werte: dict[str, float], gesamt: float,
               abweichungen: dict[str, float]) -> list[Order]:
    """Regel 11, 12, 14, 17: zu grosse Papiere verkaufen – höchstens die Abweichung, die eigenen Stück und
    max_pro_order."""
    g = einstellungen.grenzen
    if regel_12_erstmals_ueber_mindestdepot(gesamt, lage.mindestdepot_erreicht, g.mindestdepot):
        return []
    orders = []
    for papier, abweichung in abweichungen.items():
        if not regel_17_darf_anfassen(papier, einstellungen.ziel, einstellungen.abbau):
            continue
        if not regel_11_umschichten(gesamt, abweichung, g.mindestdepot, g.schwelle):
            continue
        stueck = regel_14_max_verkaufbar(lage.stueck.get(papier, 0.0), lage.eigene_stueck.get(papier, 0.0))
        betrag = _auf_rappen(min(abweichung / 100 * gesamt, stueck * lage.kurse[papier].wert, g.max_pro_order))
        if betrag >= g.mindestbetrag:
            orders.append(Order(papier, VERKAUF, betrag))
    return orders


def _kauf(einstellungen: Einstellungen, gesamt: float, abweichungen: dict[str, float],
          verfuegbar: float) -> Order | None:
    """Regel 8, 9, 10, 13, 16, 17: ein Kauf mit dem verfügbaren Geld."""
    g = einstellungen.grenzen
    kaufbar = [p for p in regel_10_kaufbar(gesamt, g.mindestdepot, einstellungen.ziel)
               if regel_13_darf_kaufen(p, einstellungen.abbau)
               and regel_17_darf_anfassen(p, einstellungen.ziel, einstellungen.abbau)]
    papier = regel_8_unterdecktestes(abweichungen, kaufbar)
    if papier is None and gesamt < g.mindestdepot and kaufbar:
        papier = kaufbar[0]  # Regel 10: unter dem Mindestdepot geht alles in Wertpapier 1
    if papier is None:
        return None
    if gesamt < g.mindestdepot:
        luecke = 0.0  # Regel 10: unter dem Mindestdepot gilt die Aufteilung noch nicht – alles verfügbare Geld
    else:
        luecke = max(-abweichungen.get(papier, 0.0), 0.0) / 100 * gesamt
    betrag = regel_9_kaufbetrag(verfuegbar, luecke, g.mindestbetrag, g.max_pro_order)
    if betrag <= 0 or not regel_16_kauf_gedeckt(betrag, verfuegbar):
        return None
    return Order(papier, KAUF, betrag)
