"""Lotse – alle Regeln. Das ist Tims Datei.

Hier steht nur Rechnen und Entscheiden: Zahlen rein, Zahlen raus. Kein Broker, kein Internet, keine Uhr.
Was eine Regel von aussen braucht (Kurse, Datum, Tagebuch), bekommt sie als Argument.
Importiert wird nur die Standardbibliothek.

Die Regeln stehen im Auftrag (AUFTRAG_LOTSE_1, Abschnitt 4). Jede Funktion sagt, welche Regel sie ist.
Solange eine Funktion `NotImplementedError` wirft, ist sie noch nicht geschrieben – der Test dazu ist rot.

Ein Wertpapier heisst hier immer mit seinem Börsenkürzel, z.B. "VWRL". Die Reihenfolge in `ziel` ist die
Reihenfolge aus config.toml: das erste ist "Wertpapier 1".
"""
from __future__ import annotations

import datetime as dt
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
    investiert          CHF, die Lotse bisher netto eingesetzt hat: Käufe minus Verkäufe, Menge × Preis (Tagebuch)
    kurse               Kurs je Wertpapier aus Ziel- und Abbau-Liste
    letzter_handelstag  der letzte abgeschlossene Handelstag der SIX (für "Kurs zu alt")
    gesamt_letzter_lauf Depotwert beim letzten Lauf in CHF (None = erster Lauf)
    heute_betrag        heute schon bestellt, in CHF
    heute_orders        heute schon bestellt, Anzahl Orders
    """
    cash: float | None
    stueck: dict[str, float]
    eigene_stueck: dict[str, float]
    kurse: dict[str, Kurs]
    letzter_handelstag: dt.date
    gesamt_letzter_lauf: float | None = None
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
    raise NotImplementedError("Regel 0 — Tim")


# --------------------------------------------------------------------------- Block 2 – Rechnen
def regel_5_werte(stueck: dict[str, float], kurse: dict[str, Kurs]) -> dict[str, float]:
    """Regel 5a: Wert jeder Position in CHF = Stück × Kurs.

    Rein:   Stück je Papier und Kurse je Papier.
    Raus:   Wert je Papier in CHF – nur für Papiere, für die es einen Kurs gibt.
    """
    raise NotImplementedError("Regel 5 — Tim")


def regel_5_gesamt(werte: dict[str, float], cash: float) -> float:
    """Regel 5b: Gesamtwert des Depots = Summe aller Werte + Cash, in CHF."""
    raise NotImplementedError("Regel 5 — Tim")


def regel_6_abweichungen(werte: dict[str, float], gesamt: float, ziel: dict[str, float]) -> dict[str, float]:
    """Regel 6: Wie weit liegt jedes Zielpapier neben seinem Ziel?

    Rein:   Werte je Papier (CHF), Gesamtwert (CHF), Zielanteile (Prozent).
    Raus:   je Zielpapier: Ist-Anteil minus Ziel-Anteil, in Prozentpunkten vom Gesamtdepot.
            Negativ = zu wenig da (Unterdeckung), positiv = zu viel.
    Beispiel: Ziel 15 %, Ist 10 % → −5.
    """
    raise NotImplementedError("Regel 6 — Tim")


def regel_7_verfuegbar(cash: float, budget: float, investiert: float, gebuehr: float,
                       puffer_prozent: float) -> float:
    """Regel 7: Wie viel CHF darf Lotse ausgeben?

    Rein:   Cash (CHF), Budget (CHF), bisher investiert (CHF), geschätzte Gebühr einer Order (CHF),
            Puffer in Prozent.
    Raus:   Grundbetrag = das Kleinere von Cash und (Budget − investiert).
            Verfügbar = Grundbetrag minus Gebühr minus Puffer (Prozent vom Grundbetrag). Nie negativ.
    Das Budget trennt Lotse vom Geld von Bot C auf demselben Paper-Konto.
    """
    raise NotImplementedError("Regel 7 — Tim")


# --------------------------------------------------------------------------- Block 3 – Entscheiden
def regel_8_unterdecktestes(abweichungen: dict[str, float], kaufbar: list[str]) -> str | None:
    """Regel 8: Welches Papier bekommt das freie Geld zuerst?

    Rein:   Abweichungen je Papier (Prozentpunkte, Regel 6) und die Papiere, die gekauft werden dürfen.
    Raus:   das kaufbare Papier mit der grössten Unterdeckung (am weitesten im Minus).
            None, wenn kein kaufbares Papier im Minus ist.
    """
    raise NotImplementedError("Regel 8 — Tim")


def regel_9_kaufbetrag(verfuegbar: float, luecke: float, mindestbetrag: float, max_pro_order: float) -> float:
    """Regel 9: Für wie viel CHF wird gekauft?

    Rein:   verfügbares Geld (Regel 7), Lücke des Papiers in CHF (wie viel bis zum Ziel fehlt), Mindestbetrag,
            Höchstbetrag pro Order.
    Raus:   Betrag in CHF; 0 heisst: nicht kaufen.
    Ist die Lücke kleiner als der Mindestbetrag, wird das ganze verfügbare Geld genommen, sonst die Lücke.
    Der Betrag ist nie grösser als das verfügbare Geld und wird auf max_pro_order gekappt.
    Liegt der Orderbetrag unter dem Mindestbetrag, wird nicht gekauft.
    """
    raise NotImplementedError("Regel 9 — Tim")


def regel_10_kaufbar(gesamt: float, mindestdepot: float, ziel: dict[str, float]) -> list[str]:
    """Regel 10: Welche Zielpapiere dürfen bei dieser Depotgrösse gekauft werden?

    Rein:   Gesamtwert (CHF), Mindestdepot (CHF), Zielanteile (Reihenfolge wie in config.toml).
    Raus:   unter dem Mindestdepot nur Wertpapier 1, sonst alle Zielpapiere.
    """
    raise NotImplementedError("Regel 10 — Tim")


def regel_11_umschichten(gesamt: float, abweichung: float, mindestdepot: float, schwelle: float) -> bool:
    """Regel 11: Darf ein zu grosses Papier verkauft werden, um umzuschichten?

    Rein:   Gesamtwert (CHF), Abweichung dieses Papiers (Prozentpunkte, positiv = zu viel), Mindestdepot, Schwelle.
    Raus:   True nur, wenn das Depot über dem Mindestdepot liegt UND die Abweichung grösser als die Schwelle ist.
    """
    raise NotImplementedError("Regel 11 — Tim")


def regel_12_erstmals_ueber_mindestdepot(gesamt: float, gesamt_letzter_lauf: float | None,
                                         mindestdepot: float) -> bool:
    """Regel 12: Ist das Depot gerade zum ersten Mal über das Mindestdepot gestiegen?

    Rein:   Gesamtwert jetzt, Gesamtwert beim letzten Lauf (None = erster Lauf), Mindestdepot.
    Raus:   True, wenn es jetzt darüber liegt und beim letzten Lauf darunter lag.
            Dann wird in diesem Lauf nicht umgeschichtet – Einzahlungen füllen den zweiten Teil auf.
    """
    raise NotImplementedError("Regel 12 — Tim")


def regel_13_darf_kaufen(papier: str, abbau: tuple[str, ...]) -> bool:
    """Regel 13: Papiere auf der Abbau-Liste werden nur verkauft, nie gekauft. Raus: False, wenn auf der Liste."""
    raise NotImplementedError("Regel 13 — Tim")


# --------------------------------------------------------------------------- Block 4 – Absichern
def regel_14_max_verkaufbar(stueck_broker: float, stueck_eigen: float) -> float:
    """Regel 14: Nie mehr Stück verkaufen, als Lotse selbst gekauft hat.

    Rein:   Stück laut Broker und Stück laut eigenem Zähler (Regel 15).
    Raus:   höchstens so viele Stück dürfen verkauft werden.
    """
    raise NotImplementedError("Regel 14 — Tim")


def regel_16_kauf_gedeckt(betrag: float, verfuegbar: float) -> bool:
    """Regel 16: Nie für mehr kaufen als verfügbar. Raus: True, wenn der Betrag gedeckt ist."""
    raise NotImplementedError("Regel 16 — Tim")


def regel_17_darf_anfassen(papier: str, ziel: dict[str, float], abbau: tuple[str, ...]) -> bool:
    """Regel 17: Lotse fasst nur Papiere an, die in der Ziel- oder Abbau-Liste stehen. Raus: True/False."""
    raise NotImplementedError("Regel 17 — Tim")


def regel_18_grenzen(order: Order, heute_betrag: float, heute_orders: int, grenzen: Grenzen) -> Abbruch | None:
    """Regel 18: Hält diese Order die Tagesgrenzen ein? (Nur noch Nachprüfung – Regel 9 kappt schon vorher.)

    Rein:   die geplante Order, heute schon bestellter Betrag (CHF) und Anzahl Orders, die Grenzen.
    Raus:   None, wenn alles passt. Abbruch mit Grund, wenn die Order über max_pro_order liegt, der Tag damit
            über max_pro_tag käme oder es mehr als max_orders_pro_tag Orders würden.
    """
    raise NotImplementedError("Regel 18 — Tim")


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
    raise NotImplementedError("Regel 20 — Tim")


# --------------------------------------------------------------------------- alles zusammen
def plane(lage: Lage, einstellungen: Einstellungen) -> Plan:
    """Der ganze Entscheid eines Laufs: verbindet Regel 0, 5–14, 16–18.

    Rein:   die gelesene Lage und die Einstellungen.
    Raus:   ein Plan – Orders (leer = nichts zu tun) oder ein Abbruch (Regel 0) mit Grund.
    Reihenfolge wie im Auftrag: prüfen (0) → rechnen (5–7) → entscheiden (8–13) → absichern (14, 16–18).
    """
    raise NotImplementedError("Plan (Regel 0, 5–18) — Tim")
