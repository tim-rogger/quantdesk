"""Lotse – Gerüst: Config, Konto (IBKR gemockt), Push, Lauf, Börsenkalender, Grenzen von logik.py."""
import ast
import datetime as dt
import math
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from lotse import lauf, logik
from lotse.konto import Konto, KontoFehler, auf_tick, echte_zahl
from lotse.push import Push
from lotse.tagebuch import VORSCHLAG, Tagebuch
from tests.lotse_fakes import UNSET_DOUBLE, FakeIB

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
def test_committete_config_ist_vorschlag_mit_offenen_wertpapieren():
    cfg, _ = lauf.lies_config()
    assert cfg["modus"] == "VORSCHLAG"  # nie etwas anderes committen
    assert set(cfg["wertpapiere"].values()) == {"TODO"}  # nichts Erfundenes
    assert cfg["push"]["thema"] == "lotse" and cfg["verbindung"]["konto"].startswith("DU")
    assert cfg["verbindung"]["client_id"] != 17  # 17 = Bot C
    assert lauf.pruefe_config(cfg) == ["Wertpapiere noch nicht gewählt: aktien, anleihen (siehe lotse/WERTPAPIERE.md)"]


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
def konto(ib=None):
    return Konto("ib-gateway", 4004, 27, "DUO844164", "EBS", "CHF", ib=ib or FakeIB())


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
    assert kurse["VWRL"] == logik.Kurs(155.3, dt.date(2026, 10, 9))
    assert kurse["CHCORP"] == logik.Kurs(99.5, dt.date(2026, 10, 8))  # kein letzter Kurs -> Schlusskurs
    assert kurse["FEHLT"].wert is None


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
    cfg = {**cfg, "modus": modus, "wertpapiere": wertpapiere or {"aktien": "VWRL", "anleihen": "CHCORP"}}
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


def test_paper_laeuft_bis_zur_logik(tmp_path):
    ergebnis, push, ib = lauf_mit(tmp_path, "PAPER")  # Budget trennt Lotse von Bot C – PAPER ist offen
    assert not ergebnis.ok and "Logik noch nicht fertig" in ergebnis.text and ib.gesendet == []


def test_offene_wertpapiere_stoppen_den_lauf(tmp_path):
    ergebnis, push, _ = lauf_mit(tmp_path, wertpapiere={"aktien": "TODO", "anleihen": "TODO"})
    assert not ergebnis.ok and "WERTPAPIERE.md" in ergebnis.text


def test_solange_die_logik_fehlt_stoppt_der_lauf_mit_hinweis(tmp_path):
    ergebnis, push, ib = lauf_mit(tmp_path)
    assert not ergebnis.ok and "Logik noch nicht fertig" in ergebnis.text and "Tim" in ergebnis.text
    assert ib.gesendet == [] and Tagebuch(str(tmp_path)).gesamt_letzter_lauf() is None


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
    assert (lage.heute_betrag, lage.heute_orders, lage.investiert) == (0.0, 0, 0.0)
