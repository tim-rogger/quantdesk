"""Lotse – Tagebuch (Regel 1, 15, 19): Lauf-Nummern, Orders vor dem Senden notieren, nichts doppelt senden,
eigener Zähler nur aus gemeldeten Ausführungen (nie nan/inf/Platzhalter)."""
import datetime as dt
import json
import math

import pytest

from lotse import lauf, logik
from lotse.konto import Konto
from lotse.push import Push
from lotse.tagebuch import (
    AUSGEFUEHRT,
    GESENDET,
    NOTIERT,
    STORNIERT,
    VERWORFEN,
    VORSCHLAG,
    Tagebuch,
    gueltige_menge,
)
from tests.lotse_fakes import KURSE_TEST, UNSET_DOUBLE, FakeIB, Preisbuch

JETZT = dt.datetime(2026, 10, 9, 10, 30)


def test_lauf_nummern_steigen_und_zieldatei_steht_im_tagebuch(tmp_path):
    tb = Tagebuch(str(tmp_path))
    assert tb.neuer_lauf("modus = 'VORSCHLAG'", JETZT, "VORSCHLAG") == 1
    tb.lauf_ende(1, "nichts", JETZT, gesamt=1950.0)
    assert Tagebuch(str(tmp_path)).neuer_lauf("x", JETZT, "VORSCHLAG") == 2  # auch nach Neustart
    erster = json.loads((tmp_path / "laeufe.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert erster["zieldatei"] == "modus = 'VORSCHLAG'"
    assert tb.gesamt_letzter_lauf() == 1950.0


def test_order_wird_vor_dem_senden_notiert_und_nie_zweimal(tmp_path):
    tb = Tagebuch(str(tmp_path))
    ref = tb.notiere(1, 1, "VWRL", "KAUF", 96.0, 0.6, 156.0, JETZT)
    assert ref == "lotse-1-1" and tb.zustaende() == {ref: NOTIERT}
    with pytest.raises(ValueError):
        tb.notiere(1, 1, "VWRL", "KAUF", 96.0, 0.6, 156.0, JETZT)
    tb.abhaken(ref, GESENDET, JETZT, perm_id=5001)
    assert tb.zustaende()[ref] == GESENDET and tb.gesendet_offen() == [ref]
    with pytest.raises(KeyError):
        tb.abhaken("lotse-9-9", GESENDET, JETZT)


def test_heute_zaehlt_bestellte_orders_aber_keine_vorschlaege(tmp_path):
    tb = Tagebuch(str(tmp_path))
    a = tb.notiere(1, 1, "VWRL", "KAUF", 200.0, 1, 156, JETZT)
    tb.abhaken(a, GESENDET, JETZT)
    b = tb.notiere(1, 2, "VWRL", "KAUF", 300.0, 1, 156, JETZT)
    tb.abhaken(b, VORSCHLAG, JETZT)
    tb.notiere(1, 3, "CHCORP", "KAUF", 50.0, 1, 100, JETZT - dt.timedelta(days=1))  # gestern
    assert tb.heute(JETZT.date()) == (200.0, 1)


# ---------------------------------------------------------------- Regel 15: eigener Zähler
def test_nur_ausgefuehrte_menge_erhoeht_den_zaehler(tmp_path):
    tb = Tagebuch(str(tmp_path))
    ref = tb.notiere(1, 1, "VWRL", "KAUF", 300.0, 2.0, 156.0, JETZT)  # bestellt: 2 Stück
    assert tb.buche_ausfuehrung(ref, "E1", "VWRL", "KAUF", 0.5, 155.0, JETZT)  # ausgeführt: 0.5 Stück
    assert tb.eigene_stueck() == {"VWRL": 0.5}


@pytest.mark.parametrize("menge", [UNSET_DOUBLE, math.nan, math.inf, -math.inf, 0.0, -1.0, None, "2", True])
def test_platzhalter_nan_inf_erhoehen_den_zaehler_nicht(tmp_path, menge):
    tb = Tagebuch(str(tmp_path))
    ref = tb.notiere(1, 1, "VWRL", "KAUF", 300.0, 2.0, 156.0, JETZT)
    assert tb.buche_ausfuehrung(ref, "E1", "VWRL", "KAUF", menge, 155.0, JETZT) is False
    assert tb.eigene_stueck() == {}
    assert not gueltige_menge(menge)


def test_unset_double_als_filled_quantity_erhoeht_den_zaehler_nicht(tmp_path):
    """Bot-C-Bug: Offene Orders tragen filledQuantity = 1.8e308. Lotse liest Mengen nur aus Ausführungen."""
    ib = FakeIB()
    ib.offene_order("lotse-1-1")
    assert ib.offen[0].order.filledQuantity == UNSET_DOUBLE
    tb = Tagebuch(str(tmp_path))
    tb.notiere(1, 1, "VWRL", "KAUF", 300.0, 2.0, 156.0, JETZT)
    tb.abhaken("lotse-1-1", GESENDET, JETZT)
    konto = Konto("h", 1, 27, "DUO844164", "EBS", "CHF", ib=ib)
    lauf.gleiche_ab(konto, tb, JETZT)
    assert tb.eigene_stueck() == {}  # keine Ausführung gemeldet -> Zähler bleibt 0
    ib.ausfuehrung("lotse-1-1", "E9", menge=UNSET_DOUBLE)  # selbst ein Platzhalter in der Ausführung zählt nicht
    lauf.gleiche_ab(konto, tb, JETZT)
    assert tb.eigene_stueck() == {}


def test_investiert_ist_netto_verbrauchtes_cash_inklusive_gebuehren(tmp_path):
    tb = Tagebuch(str(tmp_path))
    k = tb.notiere(1, 1, "VWRL", "KAUF", 300.0, 2.0, 156.0, JETZT)
    tb.buche_ausfuehrung(k, "E1", "VWRL", "KAUF", 1.5, 150.0, JETZT, gebuehr=2.0)  # 225 + 2
    v = tb.notiere(2, 1, "VWRL", "VERKAUF", 100.0, 0.5, 150.0, JETZT)
    tb.buche_ausfuehrung(v, "E2", "VWRL", "VERKAUF", 0.5, 160.0, JETZT)  # Erlös 80, Gebühr kommt später
    assert tb.investiert() == pytest.approx(147.0) and tb.ohne_gebuehr() == ["E2"]
    assert tb.buche_gebuehr("E2", 1.5, JETZT)  # Erlös nach Kommission: 78.50
    assert not tb.buche_gebuehr("E2", 1.5, JETZT)  # nie doppelt
    assert tb.investiert() == pytest.approx(148.5) and tb.ohne_gebuehr() == []
    assert Tagebuch(str(tmp_path / "leer")).investiert() == 0.0


@pytest.mark.parametrize("gebuehr", [UNSET_DOUBLE, math.nan, math.inf, -1.0, None, "2", True])
def test_ungueltige_gebuehr_wird_nicht_gebucht(tmp_path, gebuehr):
    tb = Tagebuch(str(tmp_path))
    k = tb.notiere(1, 1, "VWRL", "KAUF", 300.0, 2.0, 156.0, JETZT)
    tb.buche_ausfuehrung(k, "E1", "VWRL", "KAUF", 1.0, 150.0, JETZT)
    assert tb.buche_gebuehr("E1", gebuehr, JETZT) is False and tb.investiert() == pytest.approx(150.0)
    assert not tb.buche_gebuehr("E9", 1.0, JETZT)  # Ausführung unbekannt


def test_mindestdepot_vermerk_einmal_gesetzt_nie_wieder(tmp_path):
    tb = Tagebuch(str(tmp_path))
    assert tb.mindestdepot_erreicht() is False
    assert tb.vermerke_mindestdepot(3, 2010.0, JETZT) is True
    assert tb.vermerke_mindestdepot(4, 2500.0, JETZT) is False
    assert Tagebuch(str(tmp_path)).mindestdepot_erreicht() is True  # bleibt auch nach Neustart
    eintraege = [z for z in (tmp_path / "laeufe.jsonl").read_text(encoding="utf-8").splitlines() if "mindestdepot" in z]
    assert len(eintraege) == 1


def test_ausfuehrung_doppelt_fremd_oder_zu_viel_verkauft_wird_nicht_gebucht(tmp_path):
    tb = Tagebuch(str(tmp_path))
    ref = tb.notiere(1, 1, "VWRL", "KAUF", 300.0, 2.0, 156.0, JETZT)
    assert tb.buche_ausfuehrung(ref, "E1", "VWRL", "KAUF", 2.0, 155.0, JETZT)
    assert not tb.buche_ausfuehrung(ref, "E1", "VWRL", "KAUF", 2.0, 155.0, JETZT)  # schon gebucht
    assert not tb.buche_ausfuehrung("fremd-7", "E2", "VWRL", "KAUF", 1.0, 155.0, JETZT)  # keine Lotse-Order
    v = tb.notiere(2, 1, "VWRL", "VERKAUF", 500.0, 3.0, 150.0, JETZT)
    assert not tb.buche_ausfuehrung(v, "E3", "VWRL", "VERKAUF", 3.0, 150.0, JETZT)  # Lotse hat nur 2
    assert tb.buche_ausfuehrung(v, "E4", "VWRL", "VERKAUF", 1.5, 150.0, JETZT)
    assert tb.eigene_stueck() == {"VWRL": pytest.approx(0.5)}
    assert "NaN" not in (tmp_path / "ausfuehrungen.jsonl").read_text(encoding="utf-8")


# ---------------------------------------------------------------- Regel 19: nach Absturz nichts doppelt
def paper_lauf(tmp_path, monkeypatch, ib, plan):
    """Einen PAPER-Lauf mit fester Order-Liste (statt Tims Logik) und festem Stück/Limit ausführen."""
    monkeypatch.setattr(logik, "plane", lambda lage, e: plan)
    monkeypatch.setattr(logik, "regel_20_stueck_und_limit", lambda order, kurs, abstand, stellen: (0.5, 156.0))
    cfg, text = lauf.lies_config()
    cfg = {**cfg, "kurse": KURSE_TEST, "modus": "PAPER", "wertpapiere": {"aktien": "VWRL", "anleihen": "CHCORP"}}
    ib.preise = {"VWRL": (155.3, 155.0, dt.datetime(2026, 10, 9, 10, 0)),
                 "CHCORP": (99.0, 99.0, dt.datetime(2026, 10, 9, 10, 0))}
    konto = Konto("h", 1, 27, "DUO844164", "EBS", "CHF", ib=ib, marktdaten=Preisbuch(ib))
    return lauf.laufe(cfg, text, konto, Tagebuch(str(tmp_path)), Push(), JETZT,
                      handelstag=lambda d: dt.date(2026, 10, 8))


def test_abbruch_nach_notierter_unbestaetigter_order_naechster_lauf_sendet_sie_nicht(tmp_path, monkeypatch):
    tb = Tagebuch(str(tmp_path))
    tb.neuer_lauf("x", JETZT, "PAPER")
    alt = tb.notiere(1, 1, "VWRL", "KAUF", 96.0, 0.6, 156.0, JETZT)  # Absturz: notiert, nie gesendet
    ib = FakeIB()
    ergebnis = paper_lauf(tmp_path, monkeypatch, ib, logik.Plan([]))  # nächster Lauf
    assert ergebnis.ok
    assert ib.gesendet == []  # nichts erneut gesendet
    assert Tagebuch(str(tmp_path)).zustaende()[alt] == VERWORFEN


def test_unbestaetigte_order_die_doch_beim_broker_ist_wird_storniert_nicht_wiederholt(tmp_path, monkeypatch):
    tb = Tagebuch(str(tmp_path))
    tb.neuer_lauf("x", JETZT, "PAPER")
    alt = tb.notiere(1, 1, "VWRL", "KAUF", 96.0, 0.6, 156.0, JETZT)
    ib = FakeIB()
    ib.offene_order(alt)  # kam vor dem Absturz noch an
    paper_lauf(tmp_path, monkeypatch, ib, logik.Plan([]))
    assert ib.storniert == [alt] and ib.gesendet == []  # Regel 4: erst stornieren, nichts doppelt
    assert Tagebuch(str(tmp_path)).zustaende()[alt] == STORNIERT


def test_gesendete_und_ausgefuehrte_order_wird_gebucht_und_abgeschlossen(tmp_path, monkeypatch):
    tb = Tagebuch(str(tmp_path))
    tb.neuer_lauf("x", JETZT, "PAPER")
    alt = tb.notiere(1, 1, "VWRL", "KAUF", 96.0, 0.6, 156.0, JETZT)
    tb.abhaken(alt, GESENDET, JETZT, perm_id=4711)
    ib = FakeIB()
    ib.ausfuehrung(alt, "E1", menge=0.6, preis=155.2)  # Kommission noch nicht gemeldet
    paper_lauf(tmp_path, monkeypatch, ib, logik.Plan([]))
    neu = Tagebuch(str(tmp_path))
    assert neu.zustaende()[alt] == AUSGEFUEHRT and neu.eigene_stueck() == {"VWRL": pytest.approx(0.6)}
    assert neu.investiert() == pytest.approx(0.6 * 155.2) and neu.ohne_gebuehr() == ["E1"]
    ib.fills = []
    ib.ausfuehrung(alt, "E1", menge=0.6, preis=155.2, kommission=1.5)  # Kommission kommt beim nächsten Lauf
    paper_lauf(tmp_path, monkeypatch, ib, logik.Plan([]))
    assert Tagebuch(str(tmp_path)).investiert() == pytest.approx(0.6 * 155.2 + 1.5)


def test_neue_order_wird_notiert_dann_gesendet(tmp_path, monkeypatch):
    ib = FakeIB()
    ergebnis = paper_lauf(tmp_path, monkeypatch, ib, logik.Plan([logik.Order("VWRL", logik.KAUF, 96.0)]))
    assert ergebnis.ok and ergebnis.orders == 1
    (symbol, order), = ib.gesendet
    assert (symbol, order.orderRef, order.tif, order.outsideRth) == ("VWRL", "lotse-1-1", "DAY", False)
    assert Tagebuch(str(tmp_path)).zustaende() == {"lotse-1-1": GESENDET}


# ---------------------------------------------------------------- Kasse (Einzahlungsprüfung)
def test_kasse_des_letzten_erfolgreichen_laufs(tmp_path):
    tb = Tagebuch(str(tmp_path))
    assert tb.kasse_letzter_lauf() is None
    tb.vermerke_kasse(1, 500.0, 0.0, JETZT)
    tb.vermerke_kasse(2, 1200.0, 300.0, JETZT)
    assert Tagebuch(str(tmp_path)).kasse_letzter_lauf() == (1200.0, 300.0)


# ---------------------------------------------------------------- Ausschüttungen
def gekauft(tb, papier="CSBGC7", menge=2.0, preis=100.0, wann=dt.datetime(2026, 9, 1, 10, 0), nr=1):
    ref = tb.notiere(nr, 1, papier, "KAUF", menge * preis, menge, preis, JETZT)
    tb.buche_ausfuehrung(ref, f"E{nr}", papier, "KAUF", menge, preis, JETZT, gebuehr=0.0, ausgefuehrt=wann)


def test_ausschuettung_eigener_position_senkt_investiert(tmp_path):
    tb = Tagebuch(str(tmp_path))
    gekauft(tb)  # 200 investiert
    assert tb.buche_dividende("D1", "CSBGC7", 4.0, dt.date(2026, 10, 5), 2.0, JETZT) == pytest.approx(4.0)
    assert tb.buche_dividende("Q1", "CSBGC7", -1.4, dt.date(2026, 10, 5), 2.0, JETZT) == pytest.approx(-1.4)
    assert tb.investiert() == pytest.approx(200.0 - 2.6) and tb.dividenden() == pytest.approx(2.6)


def test_ausschuettung_fremder_papiere_zaehlt_nicht(tmp_path):
    tb = Tagebuch(str(tmp_path))
    gekauft(tb)
    assert tb.buche_dividende("D2", "AAPL", 12.0, dt.date(2026, 10, 5), 10.0, JETZT) is None  # z.B. Bot C
    assert tb.investiert() == pytest.approx(200.0)


def test_ausschuettung_vor_dem_eigenen_kauf_zaehlt_nicht(tmp_path):
    tb = Tagebuch(str(tmp_path))
    gekauft(tb, wann=dt.datetime(2026, 10, 7, 10, 0))
    assert tb.buche_dividende("D3", "CSBGC7", 4.0, dt.date(2026, 10, 5), 2.0, JETZT) is None


def test_ausschuettung_nur_lotses_anteil(tmp_path):
    tb = Tagebuch(str(tmp_path))
    gekauft(tb)  # Lotse 2 Stück, im Konto 4 (2 gehören jemand anderem)
    assert tb.buche_dividende("D4", "CSBGC7", 8.0, dt.date(2026, 10, 5), 4.0, JETZT) == pytest.approx(4.0)


def test_ausschuettung_doppelt_oder_ungueltig_wird_nicht_gebucht(tmp_path):
    tb = Tagebuch(str(tmp_path))
    gekauft(tb)
    assert tb.buche_dividende("D5", "CSBGC7", 4.0, dt.date(2026, 10, 5), 2.0, JETZT) is not None
    assert tb.buche_dividende("D5", "CSBGC7", 4.0, dt.date(2026, 10, 5), 2.0, JETZT) is None
    for betrag in (math.nan, math.inf, UNSET_DOUBLE, None, "4"):
        assert tb.buche_dividende("D6", "CSBGC7", betrag, dt.date(2026, 10, 5), 2.0, JETZT) is None
    assert tb.buche_dividende("D7", "CSBGC7", 4.0, None, 2.0, JETZT) is None
    assert tb.dividenden() == pytest.approx(4.0)
