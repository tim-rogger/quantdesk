"""Lotse – Gerüst: Config, Konto (IBKR gemockt), Push, Lauf, Börsenkalender, Grenzen von logik.py."""
import ast
import json
import datetime as dt
import math
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from lotse import lauf, logik
from lotse.konto import Konto, KontoFehler, Kursmeldung, auf_tick, echte_zahl
from lotse.push import Push
from lotse.tagebuch import VORSCHLAG, Tagebuch
from tests.lotse_fakes import KURSE_TEST, UNSET_DOUBLE, FakeIB, Preisbuch

ROOT = Path(__file__).resolve().parents[1]
JETZT = dt.datetime(2026, 10, 9, 10, 30)


# ---------------------------------------------------------------- logik.py bleibt rein
def test_logik_importiert_nur_die_standardbibliothek():
    baum = ast.parse((ROOT / "lotse" / "logik.py").read_text(encoding="utf-8"))
    module = {n.module for n in ast.walk(baum) if isinstance(n, ast.ImportFrom)} | \
             {a.name for n in ast.walk(baum) if isinstance(n, ast.Import) for a in n.names}
    fremd = {m for m in module if m.split(".")[0] not in sys.stdlib_module_names and m != "__future__"}
    assert fremd == set()


# ---------------------------------------------------------------- Config
def test_committete_config_ist_vorschlag_und_vollstaendig():
    cfg, _ = lauf.lies_config()
    assert cfg["modus"] == "VORSCHLAG"  # nie etwas anderes committen
    assert "TODO" not in cfg["wertpapiere"].values()  # von Tim gewählt (WERTPAPIERE.md)
    assert cfg["push"]["thema"] == "lotse" and cfg["verbindung"]["konto"].startswith("DU")
    assert cfg["verbindung"]["client_id"] != 17  # 17 = Bot C
    assert lauf.pruefe_config(cfg) == []


def test_offene_wertpapiere_werden_gemeldet():
    cfg, _ = lauf.lies_config()
    cfg = {**cfg, "wertpapiere": {"aktien": "TODO", "anleihen": "CSBGC7"}}
    assert lauf.pruefe_config(cfg) == ["Wertpapiere noch nicht gewählt: aktien (siehe lotse/WERTPAPIERE.md)"]


def test_einstellungen_aus_config_mit_boersenkuerzeln_in_reihenfolge():
    cfg, _ = lauf.lies_config()
    cfg = {**cfg, "wertpapiere": {"aktien": "VWRL", "anleihen": "CHCORP"}, "abbau": {"liste": ["ALT"]}}
    e = lauf.einstellungen(cfg)
    assert list(e.ziel) == ["VWRL", "CHCORP"] and e.ziel["VWRL"] == 85.0  # VWRL = Wertpapier 1
    assert e.abbau == ("ALT",) and e.grenzen.max_orders_pro_tag == 3 and e.grenzen.gebuehr_pro_order == 3.0
    assert e.grenzen.budget_chf == 300.0


def test_config_fehler_werden_benannt():
    cfg, _ = lauf.lies_config()
    kaputt = {**cfg, "modus": "LIVE", "wertpapiere": {"anleihen": "X", "aktien": "Y"}}
    probleme = lauf.pruefe_config(kaputt)
    assert any("modus" in p for p in probleme) and any("Reihenfolge" in p for p in probleme)


# ---------------------------------------------------------------- Konto
def konto(ib=None, marktdaten=None):
    ib = ib or FakeIB()
    return Konto("ib-gateway", 4004, 27, "DUO844164", "EBS", "CHF", ib=ib, marktdaten=marktdaten or Preisbuch(ib))


def test_konto_nur_paper():
    with pytest.raises(KontoFehler, match="Paper"):
        Konto("h", 1, 27, "U1234567", "EBS", "CHF", ib=FakeIB())
    k = Konto("h", 1, 27, "DUO844164", "EBS", "CHF", ib=FakeIB(konto="DU999"))
    with pytest.raises(KontoFehler, match="erwartet DUO844164"):
        k.verbinden()


def test_platzhalter_werden_none_nan_und_inf_bleiben_fuer_regel_0():
    assert echte_zahl(UNSET_DOUBLE) is None and echte_zahl(2147483647) is None and echte_zahl("x") is None
    assert math.isnan(echte_zahl(float("nan"))) and echte_zahl(float("inf")) == math.inf
    assert echte_zahl("155.30") == 155.30 and echte_zahl(0) == 0.0


def test_konto_liest_cash_chf_positionen_kurse():
    ib = FakeIB()
    ib.cash, ib.pos = 2500.0, {"VWRL": 3.25, "AAPL": 10}
    ib.preise = {"VWRL": (155.3, 154.0, dt.datetime(2026, 10, 9, 10, 0)),
                 "CHCORP": (float("nan"), 99.5, dt.datetime(2026, 10, 8, 17, 30))}
    k = konto(ib)
    k.verbinden()
    assert k.cash() == 2500.0
    assert k.positionen() == {"VWRL": 3.25, "AAPL": 10}  # alle – Regel 17 entscheidet, was Lotse anfasst
    kurse = k.kurse(["VWRL", "CHCORP", "FEHLT"])
    assert kurse["VWRL"] == Kursmeldung(155.3, dt.date(2026, 10, 9), "CHF", "Yahoo")
    assert kurse["CHCORP"] == Kursmeldung(99.5, dt.date(2026, 10, 8), "CHF", "Yahoo")
    assert kurse["FEHLT"] == Kursmeldung(None, None, None, None)


def test_konto_offene_orders_und_ausfuehrungen_nur_von_lotse():
    ib = FakeIB()
    ib.offene_order("lotse-3-1", perm=1)
    ib.offene_order("botc-ko", perm=2)  # fremde Order (z.B. Bot C)
    ib.offene_order("lotse-3-2", perm=3, konto="DU000")  # anderes Konto
    ib.ausfuehrung("lotse-3-1", "E1")
    ib.ausfuehrung("", "E2")
    k = konto(ib)
    assert [o.ref for o in k.offene_orders()] == ["lotse-3-1"]
    assert [a.exec_id for a in k.ausfuehrungen()] == ["E1"]


def test_sende_limit_nur_bis_handelsschluss_mit_tick():
    ib = FakeIB()
    ib.tick = 0.05
    perm = konto(ib).sende_limit("VWRL", logik.KAUF, 0.6407, 156.0765, "lotse-1-1")
    (_, order), = ib.gesendet
    assert perm == order.permId and order.tif == "DAY" and order.outsideRth is False
    assert order.lmtPrice == pytest.approx(156.05) and order.orderRef == "lotse-1-1" and order.account == "DUO844164"


def test_abgelehnte_order_ist_ein_fehler():
    ib = FakeIB()
    ib.ablehnen = True
    with pytest.raises(KontoFehler, match="nicht angenommen"):
        konto(ib).sende_limit("VWRL", logik.KAUF, 1, 156.0, "lotse-1-1")
    with pytest.raises(KontoFehler, match="ungültige"):
        konto(FakeIB()).sende_limit("VWRL", logik.KAUF, float("nan"), 156.0, "lotse-1-2")


@pytest.mark.parametrize("limit,seite,erwartet", [(156.0765, logik.KAUF, 156.05), (154.5235, logik.VERKAUF, 154.55),
                                                  (156.05, logik.KAUF, 156.05)])
def test_limit_auf_tick_nie_unguenstiger(limit, seite, erwartet):
    assert auf_tick(limit, 0.05, seite) == pytest.approx(erwartet)
    assert auf_tick(limit, None, seite) == limit


# ---------------------------------------------------------------- Push
def test_push_403_einmal_dann_aus(caplog):
    import requests

    aufrufe = []

    class Gesperrt:
        def post(self, url, json, headers, timeout):
            aufrufe.append(json)
            return NS(raise_for_status=lambda: (_ for _ in ()).throw(
                requests.HTTPError("403", response=NS(status_code=403))))

    p = Push("http://ntfy:80", "lotse", "", session=Gesperrt())
    assert [p.sende("Lotse", str(i)) for i in range(3)] == [False] * 3
    assert len(aufrufe) == 1 and aufrufe[0]["topic"] == "lotse" and caplog.text.count("ntfy lehnt ab") == 1
    assert Push().sende("x", "y") is False  # ohne Adresse: nichts senden


# ---------------------------------------------------------------- Börsenkalender
def test_letzter_handelstag_der_six_mit_feiertagen():
    assert lauf.letzter_handelstag(dt.date(2026, 10, 12)) == dt.date(2026, 10, 9)  # Montag -> Freitag
    assert lauf.letzter_handelstag(dt.date(2026, 4, 7)) == dt.date(2026, 4, 2)  # Karfreitag + Ostermontag zu


# ---------------------------------------------------------------- Lauf
def lauf_mit(tmp_path, modus="VORSCHLAG", ib=None, wertpapiere=None):
    cfg, text = lauf.lies_config()
    cfg = {**cfg, "kurse": KURSE_TEST, "modus": modus, "wertpapiere": wertpapiere or {"aktien": "VWRL", "anleihen": "CHCORP"}}
    ib = ib or FakeIB()
    ib.preise = ib.preise or {"VWRL": (155.3, 155.0, JETZT), "CHCORP": (99.0, 99.0, JETZT)}
    push = Push()
    ergebnis = lauf.laufe(cfg, text, konto(ib), Tagebuch(str(tmp_path)), push, JETZT,
                          handelstag=lambda d: dt.date(2026, 10, 8))
    return ergebnis, push, ib


def test_echt_ist_gesperrt(tmp_path):
    ergebnis, push, ib = lauf_mit(tmp_path, "ECHT")
    assert not ergebnis.ok and "ECHT" in ergebnis.text and ib.gesendet == []
    assert push.gesendet[0]["title"] == "Lotse – keine Order"


def test_paper_ganzer_lauf_sendet_eine_day_order_im_budget(tmp_path):
    # Konto: 10'000 CHF (gehört auch Bot C), Budget 300 → ein Kauf VWRL über 294 CHF
    ergebnis, push, ib = lauf_mit(tmp_path, "PAPER")
    assert ergebnis.ok and ergebnis.orders == 1
    (symbol, order), = ib.gesendet
    assert symbol == "VWRL" and order.action == "BUY" and order.tif == "DAY"
    assert order.totalQuantity * order.lmtPrice <= 294.0 and order.lmtPrice > 155.3
    assert Tagebuch(str(tmp_path)).zustaende() == {"lotse-1-1": "gesendet"}


def test_offene_wertpapiere_stoppen_den_lauf(tmp_path):
    ergebnis, push, _ = lauf_mit(tmp_path, wertpapiere={"aktien": "TODO", "anleihen": "TODO"})
    assert not ergebnis.ok and "WERTPAPIERE.md" in ergebnis.text


def test_vorschlag_ganzer_lauf_mit_echter_logik(tmp_path):
    ergebnis, push, ib = lauf_mit(tmp_path)
    assert ergebnis.ok and ergebnis.orders == 1 and ib.gesendet == []  # VORSCHLAG: nichts an den Broker
    assert "KAUF VWRL" in ergebnis.text and "(294.00 CHF)" in ergebnis.text
    assert Tagebuch(str(tmp_path)).gesamt_letzter_lauf() == 300.0  # Lotse-Depot: nur Lotse-Geld (Budget), nicht 10'000
    assert Tagebuch(str(tmp_path)).mindestdepot_erreicht() is False


def test_fehlender_kurs_stoppt_den_ganzen_lauf(tmp_path):
    ib = FakeIB()
    ib.preise = {"VWRL": (155.3, 155.0, JETZT)}  # CHCORP ohne Kurs
    ergebnis, push, ib = lauf_mit(tmp_path, "PAPER", ib=ib)
    assert not ergebnis.ok and "CHCORP" in ergebnis.text and ib.gesendet == []


def test_vorschlag_sendet_nichts_und_schreibt_ins_tagebuch(tmp_path, monkeypatch):
    monkeypatch.setattr(logik, "plane", lambda lage, e: logik.Plan([logik.Order("VWRL", logik.KAUF, 96.0)]))
    monkeypatch.setattr(logik, "regel_20_stueck_und_limit", lambda order, kurs, abstand, stellen: (0.6, 156.0))
    ib = FakeIB()
    ib.offene_order("lotse-0-1")  # wird im VORSCHLAG auch nicht storniert
    ergebnis, push, ib = lauf_mit(tmp_path, ib=ib)
    assert ergebnis.ok and ergebnis.orders == 1
    assert ib.gesendet == [] and ib.storniert == []  # Regel 21
    assert Tagebuch(str(tmp_path)).zustaende() == {"lotse-1-1": VORSCHLAG}
    assert "KAUF VWRL" in push.gesendet[-1]["message"] and "Vorschlag" in push.gesendet[-1]["title"]


def test_abbruch_aus_der_logik_kommt_als_push(tmp_path, monkeypatch):
    monkeypatch.setattr(logik, "plane", lambda lage, e: logik.Plan(abbruch=logik.Abbruch("Kurs CHCORP fehlt")))
    ergebnis, push, ib = lauf_mit(tmp_path)
    assert not ergebnis.ok and push.gesendet[-1]["message"] == "Kurs CHCORP fehlt" and ib.gesendet == []


def test_lage_enthaelt_was_die_logik_braucht(tmp_path, monkeypatch):
    gesehen = {}

    def plane(lage, e):
        gesehen["lage"], gesehen["e"] = lage, e
        return logik.Plan([])

    monkeypatch.setattr(logik, "plane", plane)
    ib = FakeIB()
    ib.cash, ib.pos = 1234.0, {"VWRL": 2.0, "AAPL": 5}
    lauf_mit(tmp_path, ib=ib)
    lage = gesehen["lage"]
    assert lage.cash == 1234.0 and lage.stueck == {"VWRL": 2.0, "AAPL": 5} and lage.eigene_stueck == {}
    assert set(lage.kurse) == {"VWRL", "CHCORP"} and lage.letzter_handelstag == dt.date(2026, 10, 8)
    assert (lage.heute_betrag, lage.heute_orders, lage.investiert, lage.mindestdepot_erreicht) == (0.0, 0, 0.0, False)


def test_lauf_vermerkt_mindestdepot_einmal(tmp_path, monkeypatch):
    gesehen = []
    echte_plane = logik.plane

    def plane(lage, e):
        gesehen.append(lage.mindestdepot_erreicht)
        return echte_plane(lage, e)

    monkeypatch.setattr(logik, "plane", plane)
    cfg, text = lauf.lies_config()
    cfg = {**cfg, "kurse": KURSE_TEST, "wertpapiere": {"aktien": "VWRL", "anleihen": "CHCORP"},
           "grenzen": {**cfg["grenzen"], "mindestdepot": 300}}  # Lotse-Geld 300 = genau erreicht
    ib = FakeIB()
    ib.preise = {"VWRL": (155.3, 155.0, JETZT), "CHCORP": (99.0, 99.0, JETZT)}
    for _ in range(2):
        lauf.laufe(cfg, text, konto(ib), Tagebuch(str(tmp_path)), Push(), JETZT,
                   handelstag=lambda d: dt.date(2026, 10, 8))
    assert gesehen == [False, True]  # erster Lauf: "erstmals", danach vermerkt
    assert Tagebuch(str(tmp_path)).mindestdepot_erreicht() is True


def test_konto_kommission_nur_wenn_ibkr_sie_gemeldet_hat():
    ib = FakeIB()
    ib.ausfuehrung("lotse-1-1", "E1", kommission=1.25)
    ib.ausfuehrung("lotse-1-2", "E2")  # noch keine Meldung: ib_async hat commission 0, execId leer
    gebuehren = {a.exec_id: a.gebuehr for a in konto(ib).ausfuehrungen()}
    assert gebuehren == {"E1": 1.25, "E2": None}


# ---------------------------------------------------------------- Ausschüttungen über Flex
class FakeFlex:
    zeilen = []
    aufrufe = []

    def __init__(self, token, query_id):
        FakeFlex.aufrufe.append((token, query_id))

    def extract(self, thema):
        assert thema == "CashTransaction"
        return list(FakeFlex.zeilen)


def flex_zeile(art, symbol, betrag, wann="20261005;202000", waehrung="CHF", tid="T1"):
    return NS(type=art, symbol=symbol, amount=betrag, dateTime=wann, currency=waehrung, transactionID=tid)


def test_konto_liest_ausschuettungen_aus_flex():
    FakeFlex.zeilen = [flex_zeile("Dividends", "CSBGC7", 4.0, tid="T1"),
                       flex_zeile("Withholding Tax", "CSBGC7", -1.4, tid="T2"),
                       flex_zeile("Deposits/Withdrawals", "", 500.0, tid="T3"),  # Einzahlung: keine Ausschüttung
                       flex_zeile("Dividends", "AAPL", 3.0, waehrung="USD", tid="T4")]
    k = Konto("h", 1, 27, "DUO844164", "EBS", "CHF", ib=FakeIB(), flex=FakeFlex)
    gutschriften = k.dividenden("tok", "123")
    assert [(g.id, g.papier, g.betrag, g.datum) for g in gutschriften] == [
        ("T1", "CSBGC7", 4.0, dt.date(2026, 10, 5)), ("T2", "CSBGC7", -1.4, dt.date(2026, 10, 5))]


def test_flex_fehler_wird_kontofehler():
    class Kaputt:
        def __init__(self, token, query_id):
            raise RuntimeError("Token abgelaufen")

    with pytest.raises(KontoFehler, match="Flex"):
        Konto("h", 1, 27, "DUO844164", "EBS", "CHF", ib=FakeIB(), flex=Kaputt).dividenden("t", "q")


def paper_lauf_mit_flex(tmp_path, monkeypatch, query_id="123", token="tok"):
    if token:
        monkeypatch.setenv("LOTSE_FLEX_TOKEN", token)
    else:
        monkeypatch.delenv("LOTSE_FLEX_TOKEN", raising=False)
    cfg, text = lauf.lies_config()
    cfg = {**cfg, "kurse": KURSE_TEST, "modus": "PAPER", "dividenden": {"flex_query_id": query_id}}
    ib = FakeIB()
    ib.pos = {"CSBGC7": 2.0}
    ib.preise = {"SSAC": (102.5, 102.5, dt.date(2026, 10, 8)), "CSBGC7": (98.7, 98.7, dt.date(2026, 10, 8))}
    k = Konto("h", 1, 27, "DUO844164", "EBS", "CHF", ib=ib, flex=FakeFlex, marktdaten=Preisbuch(ib))
    push = Push()
    ergebnis = lauf.laufe(cfg, text, k, Tagebuch(str(tmp_path)), push, JETZT, handelstag=lambda d: dt.date(2026, 10, 8))
    return ergebnis, push


def eigene_csbgc7(tmp_path):
    tb = Tagebuch(str(tmp_path))
    ref = tb.notiere(0, 1, "CSBGC7", "KAUF", 200.0, 2.0, 100.0, JETZT)
    tb.buche_ausfuehrung(ref, "E0", "CSBGC7", "KAUF", 2.0, 100.0, JETZT, gebuehr=0.0,
                         ausgefuehrt=dt.datetime(2026, 9, 1, 10, 0))
    return tb


def test_lauf_bucht_ausschuettung_eigener_papiere(tmp_path, monkeypatch):
    eigene_csbgc7(tmp_path)
    FakeFlex.zeilen = [flex_zeile("Dividends", "CSBGC7", 4.0, tid="T1"),
                       flex_zeile("Dividends", "NESN", 30.0, tid="T9")]  # gehört Lotse nicht
    ergebnis, push = paper_lauf_mit_flex(tmp_path, monkeypatch)
    tb = Tagebuch(str(tmp_path))
    assert tb.dividenden() == pytest.approx(4.0) and tb.investiert() == pytest.approx(196.0)
    assert "CSBGC7: Dividends +4.00 CHF gebucht (05.10.)" in push.gesendet[-1]["message"]


def test_lauf_ohne_flex_meldet_dass_ausschuettungen_fehlen(tmp_path, monkeypatch):
    eigene_csbgc7(tmp_path)
    ergebnis, push = paper_lauf_mit_flex(tmp_path, monkeypatch, query_id="", token="")
    assert "Ausschüttungen werden nicht erfasst" in push.gesendet[-1]["message"]


# ---------------------------------------------------------------- Ohne Budget: Einzahlung bestätigen
def lauf_ohne_budget(tmp_path, cash, bestaetigt=False):
    cfg, text = lauf.lies_config()
    cfg = {**cfg, "kurse": KURSE_TEST, "modus": "PAPER", "grenzen": {**cfg["grenzen"], "budget_chf": 0}}
    ib = FakeIB()
    ib.cash = cash
    ib.preise = {"SSAC": (102.5, 102.5, dt.date(2026, 10, 8)), "CSBGC7": (98.7, 98.7, dt.date(2026, 10, 8))}
    push = Push()
    ergebnis = lauf.laufe(cfg, text, konto(ib), Tagebuch(str(tmp_path)), push, JETZT,
                          handelstag=lambda d: dt.date(2026, 10, 8), einzahlung_bestaetigt=bestaetigt)
    return ergebnis, push, ib


def test_ohne_budget_grosse_einzahlung_stoppt_bis_tim_bestaetigt(tmp_path):
    ergebnis, push, ib = lauf_ohne_budget(tmp_path, 10_000.0)  # erster Lauf: alles gilt als Einzahlung
    assert not ergebnis.ok and "Ungewöhnlich grosse Einzahlung" in ergebnis.text and ib.gesendet == []
    assert push.gesendet[-1]["title"] == "Lotse – keine Order" and Tagebuch(str(tmp_path)).kasse_letzter_lauf() is None
    ergebnis, push, ib = lauf_ohne_budget(tmp_path, 10_000.0, bestaetigt=True)
    assert ergebnis.ok and len(ib.gesendet) == 1
    assert Tagebuch(str(tmp_path)).kasse_letzter_lauf() == (10_000.0, 0.0)


def test_ohne_budget_normale_einzahlung_wird_angelegt(tmp_path):
    Tagebuch(str(tmp_path)).vermerke_kasse(1, 1000.0, 0.0, JETZT)
    ergebnis, push, ib = lauf_ohne_budget(tmp_path, 1800.0)  # 800 eingezahlt
    assert ergebnis.ok and len(ib.gesendet) == 1


# ---------------------------------------------------------------- Kurse über quantdesk.marketdata (eingesetzt, kein Netz)
GESTERN = dt.date(2026, 10, 8)


def lauf_kurse(tmp_path, preise, abweichend=None, modus="PAPER"):
    cfg, text = lauf.lies_config()  # echte Config: SSAC -> SSAC.SW, CSBGC7 -> CSBGC7.SW
    cfg = {**cfg, "modus": modus}
    ib = FakeIB()
    ib.preise = preise
    quelle = Preisbuch(ib, abweichend=abweichend)
    push = Push()
    ergebnis = lauf.laufe(cfg, text, konto(ib, quelle), Tagebuch(str(tmp_path)), push, JETZT,
                          handelstag=lambda d: GESTERN)
    return ergebnis, push, ib, quelle


FRISCH = {"SSAC": (102.76, 102.76, GESTERN), "CSBGC7": (72.97, 72.97, GESTERN)}


def test_kurse_von_yahoo_order_ueber_ibkr(tmp_path):
    ergebnis, push, ib, quelle = lauf_kurse(tmp_path, FRISCH)
    assert ergebnis.ok and quelle.gefragt == ["SSAC", "CSBGC7"]
    (symbol, order), = ib.gesendet  # die Order geht über IBKR an EBS
    assert symbol == "SSAC" and order.tif == "DAY" and order.lmtPrice > 102.76
    meldung = push.gesendet[-1]["message"]
    assert "SSAC 102.76 CHF von Yahoo (08.10.)" in meldung and "CSBGC7 72.97 CHF von Yahoo (08.10.)" in meldung


def test_kursquelle_steht_im_tagebuch(tmp_path):
    lauf_kurse(tmp_path, FRISCH)
    zeilen = [json.loads(z) for z in (tmp_path / "laeufe.jsonl").read_text(encoding="utf-8").splitlines()]
    kurse = next(z["kurse"] for z in zeilen if z["ereignis"] == "kurse")
    assert kurse["SSAC"] == {"wert": 102.76, "datum": "2026-10-08", "waehrung": "CHF", "quelle": "Yahoo"}


@pytest.mark.parametrize("quelle,waehrung", [("Yahoo", "USD"), ("Stooq", None)])
def test_kurs_nicht_in_chf_regel_0_statt_umrechnen(tmp_path, quelle, waehrung):
    ergebnis, push, ib, _ = lauf_kurse(tmp_path, FRISCH, abweichend={"SSAC": (quelle, waehrung)})
    assert not ergebnis.ok and "SSAC" in ergebnis.text and "nicht umgerechnet" in ergebnis.text
    assert ib.gesendet == [] and push.gesendet[-1]["title"] == "Lotse – keine Order"


def test_kurs_von_vorgestern_regel_0_zu_alt(tmp_path):
    ergebnis, push, ib, _ = lauf_kurse(tmp_path, {**FRISCH, "CSBGC7": (72.9, 72.9, dt.date(2026, 10, 6))})
    assert not ergebnis.ok and "CSBGC7" in ergebnis.text and "zu alt" in ergebnis.text and ib.gesendet == []


def test_kein_kurs_regel_0_fehlt(tmp_path):
    ergebnis, push, ib, _ = lauf_kurse(tmp_path, {"SSAC": FRISCH["SSAC"]})
    assert not ergebnis.ok and "Kurs von CSBGC7 fehlt" in ergebnis.text


def test_config_kurs_symbole_werden_geprueft():
    cfg, _ = lauf.lies_config()
    ohne = {**cfg, "kurse": {"symbole": {"SSAC": {"yahoo": "SSAC.SW"}}}}
    assert lauf.pruefe_config(ohne)[0].startswith("[kurse.symbole] fehlt für: CSBGC7")
    leer = {**cfg, "kurse": {"symbole": {"SSAC": {"yahoo": "SSAC.SW"}, "CSBGC7": {"yahoo": "", "stooq": ""}}}}
    assert lauf.pruefe_config(leer)[0].startswith("[kurse.symbole] fehlt für: CSBGC7")
    assert cfg["kurse"]["symbole"]["SSAC"] == {"yahoo": "SSAC.SW", "stooq": ""}


def test_lauf_baut_marketdata_ohne_ibkr_mit_mapping_aus_der_config(monkeypatch):
    gebaut = {}

    class Marktdaten:
        def __init__(self, broker=None, symbole=None):
            gebaut["broker"], gebaut["symbole"] = broker, symbole

    monkeypatch.setattr("quantdesk.marketdata.MarketData", Marktdaten)
    monkeypatch.setattr(lauf, "Konto", lambda *a, **k: (_ for _ in ()).throw(KontoFehler("nur bauen (Test)")))
    assert lauf.main([]) == 1
    assert gebaut["broker"] is None and gebaut["symbole"]["CSBGC7"]["yahoo"] == "CSBGC7.SW"
