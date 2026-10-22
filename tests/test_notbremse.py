"""Notbremse: nur anhalten – eigene offene Orders stornieren, keine neuen, nie verkaufen, nie fortsetzen.

Gilt für beide Bots (Strategie C und Lotse). Das Dashboard legt nur die Markierung halt/HALT ab;
fortgesetzt wird nur im Terminal.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

import run_daily
from dashboard.app import create_app
from lotse import lauf
from lotse.push import Push
from lotse.tagebuch import Tagebuch
from quantdesk.notify import Notifier
from quantdesk.status import RESUME_COMMAND, STOP_FILE
from quantdesk.strategy import FILLED, ON, PENDING, PLACED
from tests.fakes import FakeBroker
from tests.lotse_fakes import FakeIB
from tests.test_engine import aapl_on, make_engine
from tests.test_lotse_geruest import JETZT, konto
from tests.test_server import env, fake_services, settings_for

ROOT = Path(__file__).resolve().parent.parent


# ====================================================================== Bot C: Engine
def test_halt_storniert_nur_eigene_offene_orders_und_verkauft_nie(tmp_path):
    b = FakeBroker()
    e = make_engine(b, str(tmp_path / "eq.json"))
    aapl_on(e, levels=3)
    e.run_once()
    b.fill("O1", 100.0)
    e.run_once()
    lv1, lv2, lv3 = e.systems["AAPL"].levels
    assert {lv.status for lv in (lv1, lv2, lv3)} == {PLACED}
    b.fill(lv1.order_id, lv1.price)                                   # vor dem Storno noch ausgeführt
    fremd = b.place_limit_order("AAPL", "BUY", 5, 50.0).order_id      # Tims eigene Order – nie anfassen
    vorher = list(b.placed)
    erwartet = sorted([lv2.order_id, lv3.order_id])

    e.run_once(decide=False)
    storniert = e.halt_cancel()
    e.run_once(decide=False)
    e.reopen_halted_levels(storniert)

    assert sorted(storniert) == erwartet                              # die gefüllte Stufe nicht
    assert fremd not in b.cancelled and fremd not in storniert
    assert b.placed == vorher                                         # nichts Neues, kein Verkauf
    assert lv1.status == FILLED and (lv2.status, lv3.status) == (PENDING, PENDING)
    assert e.systems["AAPL"].status == ON                             # anders als stop_all: bleibt eingeschaltet


def test_nach_dem_fortsetzen_werden_die_stufen_zum_selben_preis_neu_gesetzt(tmp_path):
    b = FakeBroker()
    e = make_engine(b, str(tmp_path / "eq.json"))
    aapl_on(e, levels=2)
    e.run_once()
    b.fill("O1", 100.0)
    e.run_once()
    preise = [lv.price for lv in e.systems["AAPL"].levels]
    storniert = e.halt_cancel()
    e.run_once(decide=False)
    e.reopen_halted_levels(storniert)
    vorher = len(b.placed)
    e.run_once()                                                      # normaler Lauf nach dem Fortsetzen
    neu = b.placed[vorher:]
    assert [(art, seite, preis) for art, _, seite, _, preis in neu] == [("LMT", "BUY", p) for p in preise]


def test_offener_einstieg_wird_storniert_und_nicht_neu_gekauft(tmp_path):
    b = FakeBroker()
    e = make_engine(b, str(tmp_path / "eq.json"))
    aapl_on(e)
    e.run_once()                                                      # Market-Buy O1 offen
    assert e.halt_cancel() == ["O1"]
    e.run_once(decide=False)
    assert e.systems["AAPL"].entry_order_id is None
    assert b.placed == [("MKT", "AAPL", "BUY", 1, None)]              # kein zweiter Kauf


# ====================================================================== Bot C: Tageslauf
def _c_lauf(tmp_path, monkeypatch, b, mode="trade"):
    monkeypatch.setattr(run_daily, "build_services", fake_services(tmp_path, b))
    monkeypatch.setattr(run_daily, "load_report", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    monkeypatch.setattr(run_daily, "PASS_WAIT_SECONDS", 0)
    return run_daily.run(mode, force=True, push=False)


def _stufen(tmp_path):
    return json.loads((tmp_path / "eq.json").read_text())["systems"][0]


def test_tageslauf_mit_notbremse_storniert_und_haelt_an(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    b = FakeBroker()
    assert _c_lauf(tmp_path, monkeypatch, b) == 0                     # Einstieg KO
    b.fill("O1", 100.0)
    assert _c_lauf(tmp_path, monkeypatch, b) == 0                     # Stufen platziert
    platziert = [o.order_id for o in b.orders_of_type("LMT")]
    assert platziert

    (tmp_path / "halt").mkdir()
    (tmp_path / "halt" / "HALT").write_text("{}")
    vorher = list(b.placed)
    for mode in ("trade", "reconcile"):
        assert _c_lauf(tmp_path, monkeypatch, b, mode) == 0
    assert sorted(b.cancelled) == sorted(platziert)
    assert b.placed == vorher and not [p for p in b.placed if p[2] == "SELL"]
    system = _stufen(tmp_path)
    assert system["status"] == "On" and {lv["status"] for lv in system["levels"]} == {"pending"}
    runs = (tmp_path / "data" / "runs.jsonl").read_text(encoding="utf-8")
    assert "Notbremse aktiv" in runs

    (tmp_path / "halt" / "HALT").unlink()                             # Fortsetzen (im Terminal)
    assert _c_lauf(tmp_path, monkeypatch, b) == 0
    assert len(b.orders_of_type("LMT")) == 2 * len(platziert)         # dieselben Stufen neu gesetzt


def test_alte_stop_datei_haelt_weiterhin_an(tmp_path, monkeypatch):
    """Liegt heute noch data/STOP auf dem Server, darf ein Update nicht still weiterhandeln."""
    env(tmp_path, monkeypatch)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / STOP_FILE).write_text("{}")
    b = FakeBroker()
    assert _c_lauf(tmp_path, monkeypatch, b) == 0
    assert b.placed == []
    assert _stufen(tmp_path)["status"] == "On"                        # nicht mehr dauerhaft "Off" wie früher


# ====================================================================== Lotse
def test_lotse_halt_aktiv(tmp_path, monkeypatch):
    halt = tmp_path / "HALT"
    assert lauf.halt_aktiv(str(halt)) is False
    halt.write_text("{}")
    assert lauf.halt_aktiv(str(halt)) is True
    monkeypatch.delenv("LOTSE_HALT_DATEI", raising=False)
    assert lauf.halt_aktiv() is False                                 # ohne Pfad: nie gehalten


def test_lotse_paper_storniert_nur_eigene_orders(tmp_path):
    ib = FakeIB()
    tb = Tagebuch(str(tmp_path))
    ref = tb.notiere(1, 1, "VWRL", "KAUF", 150.0, 1.0, 155.0, JETZT)
    tb.abhaken(ref, "gesendet", JETZT)
    ib.offene_order(ref, perm=777)
    ib.offene_order("fremd-1", perm=778)
    cfg, text = lauf.lies_config()
    push = Push()
    ergebnis = lauf.halte({**cfg, "modus": "PAPER"}, text, konto(ib), tb, push, JETZT)
    assert ib.storniert == [ref] and ib.gesendet == []
    assert tb.zustaende()[ref] == "storniert"
    assert not ergebnis.ok and "Notbremse aktiv" in ergebnis.text
    assert push.gesendet and "Notbremse" in push.gesendet[-1]["title"]


def test_lotse_vorschlag_spricht_gar_nicht_mit_dem_broker(tmp_path):
    ib = FakeIB()
    cfg, text = lauf.lies_config()
    ergebnis = lauf.halte({**cfg, "modus": "VORSCHLAG"}, text, konto(ib), Tagebuch(str(tmp_path)), Push(), JETZT)
    assert ib.verbunden is False and ib.storniert == [] and ib.gesendet == []
    zeilen = [json.loads(z) for z in (tmp_path / "laeufe.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [z["ereignis"] for z in zeilen] == ["start", "ende"]       # sichtbar für /system, ohne "kasse"
    assert "Notbremse" in zeilen[-1]["ergebnis"] and not ergebnis.ok


def test_lotse_main_prueft_die_bremse_vor_dem_lauf(tmp_path, monkeypatch):
    halt = tmp_path / "HALT"
    halt.write_text("{}")
    monkeypatch.setenv("LOTSE_HALT_DATEI", str(halt))
    monkeypatch.setenv("LOTSE_ORDNER", str(tmp_path / "tb"))
    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.setattr(lauf, "Konto", lambda *a, **k: konto(FakeIB()))
    monkeypatch.setattr(lauf, "laufe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("darf nicht laufen")))
    assert lauf.main([]) == 1                                         # 1 = bewusst gestoppt (kein Absturz-Push)
    assert "Notbremse" in (tmp_path / "tb" / "laeufe.jsonl").read_text(encoding="utf-8")


# ====================================================================== Dashboard
def test_bremse_schreibt_nur_die_halt_markierung(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch)
    push = Notifier()
    client = TestClient(create_app(s, push))
    assert client.post("/api/stop", json={"pin": "000000"}).status_code == 403
    assert not (tmp_path / "halt" / "HALT").exists()
    r = client.post("/api/stop", json={"pin": "246810"})
    assert r.status_code == 200 and (tmp_path / "halt" / "HALT").exists()
    assert not (tmp_path / "data" / STOP_FILE).exists()               # nicht mehr in die Daten von Bot C
    seit = json.loads((tmp_path / "halt" / "HALT").read_text())["ts"]
    assert client.post("/api/stop", json={"pin": "246810"}).status_code == 200
    assert json.loads((tmp_path / "halt" / "HALT").read_text())["ts"] == seit   # zweites Ziehen ändert nichts
    status = client.get("/api/status").json()
    assert status["stop_active"] is True and status["halt"]["aktiv"] is True
    assert status["halt"]["fortsetzen"] == RESUME_COMMAND
    assert client.get("/api/system").json()["halt"]["aktiv"] is True
    assert len(push.sent) == 1 and "Notbremse" in push.sent[0]["title"]


def test_dashboard_kann_nicht_fortsetzen(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch)
    app = create_app(s, Notifier())
    schreibend = [(r.path, sorted(r.methods - {"HEAD"})) for r in app.routes
                  if getattr(r, "methods", set()) - {"GET", "HEAD"}]
    assert schreibend == [("/api/stop", ["POST"])]                    # nur anhalten, nichts anderes
    client = TestClient(app)
    (tmp_path / "halt").mkdir()
    (tmp_path / "halt" / "HALT").write_text("{}")
    assert client.delete("/api/stop").status_code == 405
    assert (tmp_path / "halt" / "HALT").exists()


def test_alte_stop_datei_wird_im_dashboard_als_aktiv_gemeldet(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / STOP_FILE).write_text("{}")
    assert TestClient(create_app(s, Notifier())).get("/api/status").json()["halt"]["aktiv"] is True


def test_bremse_hat_einen_bestaetigen_schritt():
    html = (ROOT / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "dashboard" / "static" / "notbremse.js").read_text(encoding="utf-8")
    bremse = re.search(r'<section id="notbremse".*?</section>', html, re.S).group(0)
    assert html.count("<button") == bremse.count("<button") == 3     # alle Knöpfe der App gehören zur Bremse
    assert 'id="notbremse-ja"' in bremse and 'id="notbremse-abbrechen"' in bremse
    # Schritt 1 (Formular) sendet nichts – erst "Ja, beide Bots anhalten" schickt die Anfrage
    absenden = js.split('$("stop-form").addEventListener("submit"')[1].split("});")[0]
    assert "fetch" not in absenden
    assert js.count("fetch(") == 1 and '"/api/stop"' in js and 'addEventListener("click", bremseZiehen)' in js
    assert "nichts verkauft" in bremse and "nur im Terminal" in bremse


# ====================================================================== Compose: keine weiteren Schreibrechte
def test_compose_halt_ordner_und_rechte():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    dashboard = compose.split("\n  dashboard:")[1].split("\n  lotse:")[0]
    lotse = compose.split("\n  lotse:")[1].split("\nsecrets:")[0]
    beschreibbar = [z.strip() for z in dashboard.splitlines()
                    if z.strip().startswith("- ./") and not z.strip().split("#")[0].strip().endswith(":ro")]
    assert beschreibbar == ["- ./state/halt:/state/halt"]             # der einzige beschreibbare Ordner
    assert "- ./state/data:/state/data:ro" in dashboard
    assert "- ./state/halt:/state/halt:ro" in lotse and "LOTSE_HALT_DATEI: /state/halt/HALT" in lotse
    assert "docker.sock" not in compose
    assert "state/halt" in (ROOT / "deploy" / "prepare.sh").read_text(encoding="utf-8")


def test_server_md_fortsetzen_und_ports():
    server = (ROOT / "deploy" / "SERVER.md").read_text(encoding="utf-8")
    assert RESUME_COMMAND in server
    assert "--https=8443 http://127.0.0.1:8080   # Dashboard" in server
    assert "--https=443  http://127.0.0.1:8090   # ntfy" in server
