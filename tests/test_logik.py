"""Lotse – Tims Aufgabenblatt: ein Test pro Regel, mit konkreten Zahlen. Alle sind rot, bis die Regel steht.

Einen einzelnen Test laufen lassen:
    python -m pytest tests/test_logik.py -k regel_7 -v

Die Papiere heissen hier AKTIEN (Wertpapier 1) und ANLEIHEN (Wertpapier 2), beide kosten 100 CHF.
"""
import datetime as dt
import math
from dataclasses import replace

import pytest

from lotse.logik import (
    KAUF,
    VERKAUF,
    Abbruch,
    Einstellungen,
    Grenzen,
    Kurs,
    Lage,
    Order,
    plane,
    regel_0_pruefe_lage,
    regel_5_gesamt,
    regel_5_werte,
    regel_6_abweichungen,
    regel_7_verfuegbar,
    regel_8_unterdecktestes,
    regel_9_kaufbetrag,
    regel_10_kaufbar,
    regel_11_umschichten,
    regel_12_erstmals_ueber_mindestdepot,
    regel_13_darf_kaufen,
    regel_14_max_verkaufbar,
    regel_16_kauf_gedeckt,
    regel_17_darf_anfassen,
    regel_18_grenzen,
    regel_20_stueck_und_limit,
)

HANDELSTAG = dt.date(2026, 10, 9)
ZIEL = {"AKTIEN": 85.0, "ANLEIHEN": 15.0}
GRENZEN = Grenzen(mindestbetrag=100, schwelle=5, mindestdepot=2000, puffer_prozent=1, max_pro_order=500,
                  max_pro_tag=1000, max_orders_pro_tag=3, gebuehr_pro_order=3, limit_abstand_prozent=0.5,
                  bruchstueck_stellen=4)
EINSTELLUNGEN = Einstellungen(ziel=ZIEL, abbau=(), grenzen=GRENZEN)


def frisch(wert=100.0):
    return Kurs(wert, HANDELSTAG)


def lage(cash, stueck=None, eigene=None, kurse=None, gesamt_letzter_lauf=None, heute_betrag=0.0, heute_orders=0):
    stueck = stueck or {}
    return Lage(cash=cash, stueck=stueck, eigene_stueck=dict(stueck) if eigene is None else eigene,
                kurse=kurse or {"AKTIEN": frisch(), "ANLEIHEN": frisch()}, letzter_handelstag=HANDELSTAG,
                gesamt_letzter_lauf=gesamt_letzter_lauf, heute_betrag=heute_betrag, heute_orders=heute_orders)


def kaeufe(plan, papier=None):
    return [o for o in plan.orders if o.seite == KAUF and (papier is None or o.papier == papier)]


def verkaeufe(plan, papier=None):
    return [o for o in plan.orders if o.seite == VERKAUF and (papier is None or o.papier == papier)]


# =========================================================================== Regel 0 – Grundhaltung
def test_regel_0_alles_da_kein_abbruch():
    assert regel_0_pruefe_lage(lage(500), EINSTELLUNGEN) is None


def test_regel_0_kurs_fehlt_abbruch_mit_papier_im_grund():
    ergebnis = regel_0_pruefe_lage(lage(500, kurse={"AKTIEN": frisch()}), EINSTELLUNGEN)
    assert isinstance(ergebnis, Abbruch) and "ANLEIHEN" in ergebnis.grund


@pytest.mark.parametrize("wert", [0.0, -5.0, math.nan, math.inf, None])
def test_regel_0_kurs_null_negativ_nan_inf_abbruch(wert):
    ergebnis = regel_0_pruefe_lage(lage(500, kurse={"AKTIEN": frisch(), "ANLEIHEN": Kurs(wert, HANDELSTAG)}),
                                   EINSTELLUNGEN)
    assert isinstance(ergebnis, Abbruch) and "ANLEIHEN" in ergebnis.grund


def test_regel_0_kurs_aelter_als_ein_handelstag_abbruch():
    alt = Kurs(100.0, HANDELSTAG - dt.timedelta(days=3))
    ergebnis = regel_0_pruefe_lage(lage(500, kurse={"AKTIEN": alt, "ANLEIHEN": frisch()}), EINSTELLUNGEN)
    assert isinstance(ergebnis, Abbruch) and "AKTIEN" in ergebnis.grund


@pytest.mark.parametrize("cash", [None, -1.0])
def test_regel_0_cash_fehlt_oder_negativ_abbruch(cash):
    assert isinstance(regel_0_pruefe_lage(lage(cash), EINSTELLUNGEN), Abbruch)


# =========================================================================== Block 2 – Rechnen
def test_regel_5_wert_ist_stueck_mal_kurs():
    werte = regel_5_werte({"AKTIEN": 10, "ANLEIHEN": 2.5}, {"AKTIEN": frisch(12.5), "ANLEIHEN": frisch(80.0)})
    assert werte == {"AKTIEN": pytest.approx(125.0), "ANLEIHEN": pytest.approx(200.0)}


def test_regel_5_gesamt_ist_summe_plus_cash():
    assert regel_5_gesamt({"AKTIEN": 125.0, "ANLEIHEN": 200.0}, 50.0) == pytest.approx(375.0)


def test_regel_6_abweichung_in_prozentpunkten():
    abw = regel_6_abweichungen({"AKTIEN": 850.0, "ANLEIHEN": 100.0}, 1000.0, ZIEL)
    assert abw == {"AKTIEN": pytest.approx(0.0), "ANLEIHEN": pytest.approx(-5.0)}


def test_regel_7_verfuegbar_ist_cash_minus_gebuehr_minus_puffer():
    assert regel_7_verfuegbar(200.0, 3.0, 1.0) == pytest.approx(195.0)  # 200 − 3 − 1 % von 200


def test_regel_7_nie_negativ():
    assert regel_7_verfuegbar(2.0, 3.0, 1.0) == 0.0


# =========================================================================== Block 3 – Entscheiden
def test_regel_8_groesste_unterdeckung_zuerst():
    assert regel_8_unterdecktestes({"AKTIEN": -2.0, "ANLEIHEN": -6.0}, ["AKTIEN", "ANLEIHEN"]) == "ANLEIHEN"


def test_regel_8_nur_kaufbare_papiere():
    assert regel_8_unterdecktestes({"AKTIEN": -2.0, "ANLEIHEN": -6.0}, ["AKTIEN"]) == "AKTIEN"


def test_regel_8_keine_unterdeckung_kein_papier():
    assert regel_8_unterdecktestes({"AKTIEN": 1.0, "ANLEIHEN": 0.0}, ["AKTIEN", "ANLEIHEN"]) is None


def test_regel_9_luecke_groesser_als_mindestbetrag_kauf_der_luecke():
    assert regel_9_kaufbetrag(verfuegbar=300.0, luecke=200.0, mindestbetrag=100.0) == pytest.approx(200.0)


def test_regel_9_luecke_kleiner_als_mindestbetrag_ganzes_geld():
    assert regel_9_kaufbetrag(verfuegbar=145.5, luecke=80.0, mindestbetrag=100.0) == pytest.approx(145.5)


def test_regel_9_zu_wenig_geld_kein_kauf():
    assert regel_9_kaufbetrag(verfuegbar=40.0, luecke=300.0, mindestbetrag=100.0) == 0.0


def test_regel_10_unter_mindestdepot_nur_wertpapier_1():
    assert regel_10_kaufbar(1999.0, 2000.0, ZIEL) == ["AKTIEN"]


def test_regel_10_ab_mindestdepot_alle():
    assert regel_10_kaufbar(2000.0, 2000.0, ZIEL) == ["AKTIEN", "ANLEIHEN"]


@pytest.mark.parametrize("gesamt,abweichung,erwartet", [
    (5000.0, 7.0, True),    # über Mindestdepot, über Schwelle
    (5000.0, 5.0, False),   # genau auf der Schwelle: nicht darüber
    (5000.0, 4.0, False),   # unter Schwelle
    (1999.0, 7.0, False),   # unter Mindestdepot
])
def test_regel_11_umschichten_nur_ueber_mindestdepot_und_schwelle(gesamt, abweichung, erwartet):
    assert regel_11_umschichten(gesamt, abweichung, mindestdepot=2000.0, schwelle=5.0) is erwartet


@pytest.mark.parametrize("gesamt,letzter,erwartet", [
    (2100.0, 1950.0, True),   # gerade darüber gestiegen
    (2100.0, 2050.0, False),  # war schon darüber
    (1900.0, 1800.0, False),  # noch darunter
])
def test_regel_12_erstmals_ueber_mindestdepot(gesamt, letzter, erwartet):
    assert regel_12_erstmals_ueber_mindestdepot(gesamt, letzter, 2000.0) is erwartet


def test_regel_13_abbau_liste_nie_kaufen():
    assert regel_13_darf_kaufen("ANLEIHEN", ("ANLEIHEN",)) is False
    assert regel_13_darf_kaufen("AKTIEN", ("ANLEIHEN",)) is True


# =========================================================================== Block 4 – Absichern
def test_regel_14_bot_besitzt_3_hat_2_gekauft_hoechstens_2():
    assert regel_14_max_verkaufbar(stueck_broker=3.0, stueck_eigen=2.0) == pytest.approx(2.0)


def test_regel_14_broker_hat_weniger_als_eigene():
    assert regel_14_max_verkaufbar(stueck_broker=1.0, stueck_eigen=2.0) == pytest.approx(1.0)


def test_regel_16_nie_mehr_als_verfuegbar():
    assert regel_16_kauf_gedeckt(100.0, 96.0) is False
    assert regel_16_kauf_gedeckt(96.0, 96.0) is True


def test_regel_17_nur_papiere_aus_ziel_oder_abbau():
    assert regel_17_darf_anfassen("NESN", ZIEL, ("ALTFONDS",)) is False
    assert regel_17_darf_anfassen("AKTIEN", ZIEL, ()) is True
    assert regel_17_darf_anfassen("ALTFONDS", ZIEL, ("ALTFONDS",)) is True


def test_regel_18_order_ueber_max_pro_order_abbruch():
    assert isinstance(regel_18_grenzen(Order("AKTIEN", KAUF, 501.0), 0.0, 0, GRENZEN), Abbruch)


def test_regel_18_tag_ueber_max_pro_tag_abbruch():
    assert isinstance(regel_18_grenzen(Order("AKTIEN", KAUF, 300.0), 800.0, 1, GRENZEN), Abbruch)


def test_regel_18_vierte_order_am_tag_abbruch():
    assert isinstance(regel_18_grenzen(Order("AKTIEN", KAUF, 100.0), 300.0, 3, GRENZEN), Abbruch)


def test_regel_18_innerhalb_der_grenzen_kein_abbruch():
    assert regel_18_grenzen(Order("AKTIEN", KAUF, 500.0), 500.0, 2, GRENZEN) is None


# =========================================================================== Regel 20 – Rechenteil
def test_regel_20_kauf_limit_ueber_kurs_und_stueck_passen_in_den_betrag():
    stueck, limit = regel_20_stueck_und_limit(Order("AKTIEN", KAUF, 100.0), 155.30, 0.5, 4)
    assert limit == pytest.approx(155.30 * 1.005, abs=0.01)
    assert stueck * limit <= 100.0
    assert round(stueck, 4) == stueck and stueck == pytest.approx(0.6407, abs=0.0001)


def test_regel_20_verkauf_limit_unter_kurs():
    _, limit = regel_20_stueck_und_limit(Order("AKTIEN", VERKAUF, 100.0), 155.30, 0.5, 4)
    assert limit == pytest.approx(155.30 * 0.995, abs=0.01)


def test_regel_20_nur_ganze_stueck():
    stueck, limit = regel_20_stueck_und_limit(Order("AKTIEN", KAUF, 400.0), 155.30, 0.5, 0)
    assert stueck == 2.0 and stueck * limit <= 400.0


# =========================================================================== Fälle aus dem Auftrag (plane)
def test_fall_leeres_depot_100_chf_ein_kauf_wertpapier_1_knapp_unter_100():
    plan = plane(lage(100.0), EINSTELLUNGEN)
    assert plan.abbruch is None and len(plan.orders) == 1
    order = plan.orders[0]
    assert (order.papier, order.seite) == ("AKTIEN", KAUF)
    assert 90.0 < order.betrag < 100.0  # Gebühr (3) und Puffer (1 %) gehen ab


def test_fall_leeres_depot_99_chf_nichts():
    plan = plane(lage(99.0), EINSTELLUNGEN)
    assert plan.abbruch is None and plan.orders == []


def test_fall_150_chf_luecke_nur_80_kauf_ueber_ganzes_verfuegbares_geld():
    # AKTIEN 1450 CHF, ANLEIHEN 200 CHF, Cash 150 → Gesamt 1800, Ziel AKTIEN 1530 → Lücke 80 CHF
    plan = plane(lage(150.0, stueck={"AKTIEN": 14.5, "ANLEIHEN": 2.0}), EINSTELLUNGEN)
    assert plan.abbruch is None and len(kaeufe(plan)) == 1
    assert kaeufe(plan, "AKTIEN")[0].betrag == pytest.approx(145.5)  # 150 − 3 − 1.50


def test_fall_depot_1999_nur_wertpapier_1():
    plan = plane(lage(100.0, stueck={"AKTIEN": 18.99}), EINSTELLUNGEN)  # 1899 + 100 Cash = 1999
    assert plan.abbruch is None
    assert kaeufe(plan) and all(o.papier == "AKTIEN" for o in plan.orders)


def test_fall_depot_springt_auf_2100_anleihen_0_kein_verkauf():
    plan = plane(lage(100.0, stueck={"AKTIEN": 20.0}, gesamt_letzter_lauf=1950.0), EINSTELLUNGEN)
    assert plan.abbruch is None and verkaeufe(plan) == []


def test_fall_abweichung_4_prozentpunkte_depot_5000_nichts():
    plan = plane(lage(0.0, stueck={"AKTIEN": 44.5, "ANLEIHEN": 5.5}, gesamt_letzter_lauf=5000.0), EINSTELLUNGEN)
    assert plan.abbruch is None and plan.orders == []


def test_fall_abweichung_7_prozentpunkte_depot_5000_umschichtung():
    plan = plane(lage(0.0, stueck={"AKTIEN": 46.0, "ANLEIHEN": 4.0}, gesamt_letzter_lauf=5000.0), EINSTELLUNGEN)
    assert plan.abbruch is None
    verkauf = verkaeufe(plan, "AKTIEN")
    assert len(verkauf) == 1 and 0 < verkauf[0].betrag <= 350.0  # höchstens 7 % von 5000
    # Ob mit dem Erlös im selben Lauf ANLEIHEN gekauft werden, ist offen (Cash ist 0, Regel 7/16) – Tim entscheidet.


def test_fall_kurs_fehlt_abbruch_mit_grund():
    plan = plane(lage(500.0, kurse={"AKTIEN": frisch()}), EINSTELLUNGEN)
    assert plan.abbruch is not None and "ANLEIHEN" in plan.abbruch.grund and plan.orders == []


@pytest.mark.parametrize("wert", [0.0, -1.0, math.nan, math.inf])
def test_fall_kurs_null_negativ_nan_inf_abbruch(wert):
    plan = plane(lage(500.0, kurse={"AKTIEN": Kurs(wert, HANDELSTAG), "ANLEIHEN": frisch()}), EINSTELLUNGEN)
    assert plan.abbruch is not None and plan.orders == []


def test_fall_bot_besitzt_3_hat_2_gekauft_hoechstens_2_verkauft():
    # AKTIEN deutlich zu schwer: Verkauf ja, aber nie mehr als die 2 eigenen Stück (= 200 CHF)
    plan = plane(lage(0.0, stueck={"AKTIEN": 3.0}, eigene={"AKTIEN": 2.0}, gesamt_letzter_lauf=5000.0),
                 Einstellungen(ziel={"AKTIEN": 10.0, "ANLEIHEN": 90.0}, abbau=(),
                               grenzen=replace(GRENZEN, mindestdepot=100.0)))
    assert plan.abbruch is None
    assert sum(o.betrag for o in verkaeufe(plan, "AKTIEN")) <= 200.0


def test_fall_fremdes_papier_im_depot_wird_nicht_angefasst():
    plan = plane(lage(500.0, stueck={"AKTIEN": 30.0, "NESN": 10.0}), EINSTELLUNGEN)
    assert plan.abbruch is None and all(o.papier != "NESN" for o in plan.orders)


def test_fall_abbau_papier_unterdeckt_kein_kauf():
    e = Einstellungen(ziel=ZIEL, abbau=("ANLEIHEN",), grenzen=GRENZEN)
    plan = plane(lage(600.0, stueck={"AKTIEN": 30.0}, gesamt_letzter_lauf=3500.0), e)
    assert plan.abbruch is None and kaeufe(plan, "ANLEIHEN") == []


def test_fall_vierte_order_am_selben_tag_abbruch():
    plan = plane(lage(600.0, stueck={"AKTIEN": 30.0}, gesamt_letzter_lauf=3500.0, heute_betrag=300.0,
                      heute_orders=3), EINSTELLUNGEN)
    assert plan.abbruch is not None and plan.orders == []


def test_fall_cash_genau_max_pro_order_plus_gebuehr_order_hoechstens_max_pro_order():
    plan = plane(lage(503.0), EINSTELLUNGEN)
    assert plan.abbruch is None and plan.orders
    assert all(o.betrag <= 500.0 for o in plan.orders)
