"""Gateway-Selbstheilung (systemstatus/waechter.py), Sammler hängt nie am Gateway, Konto-Abzeichen mit drei Zuständen."""
from __future__ import annotations

import datetime as dt
import json
import socket
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from systemstatus import messen, tws, waechter
from systemstatus import __main__ as sammler
from systemstatus.bewerten import bewerten, konto_zustand
from tests.test_systemstatus import beispiel

ROOT = Path(__file__).resolve().parent.parent
NY = ZoneInfo("America/New_York")
MIN = 60


def ny(*args) -> float:
    return dt.datetime(*args, tzinfo=NY).timestamp()


DIENSTAG = ny(2026, 10, 27, 11, 0)  # kein Wartungsfenster


def ablauf(start: float, schritte: list[tuple[float, bool]]) -> list[list[tuple]]:
    """Wächter über mehrere Prüfungen laufen lassen: [(Minuten nach start, gesund?), …] -> Aktionen je Prüfung."""
    zustand, alle = waechter.neuer_zustand(), []
    for minuten, gesund in schritte:
        zustand, aktionen = waechter.entscheide(zustand, gesund, start + minuten * MIN)
        alle.append(aktionen)
    return alle


def arten(aktionen: list[tuple]) -> list[str]:
    return [a[0] if a[0] == "neustart" else a[1] for a in aktionen]


# ====================================================================== Wächter: Entscheidungen
def test_gesund_tut_nichts():
    assert ablauf(DIENSTAG, [(0, True), (1, True)]) == [[], []]


def test_kurzer_aussetzer_ist_kein_grund_fuer_einen_neustart():
    """z.B. der tägliche Neustart der Gateway-App: ein paar Minuten ausgeloggt ist normal."""
    alle = ablauf(DIENSTAG, [(0, False), (5, False), (8, False), (9, True)])
    assert all(a == [] for a in alle)


def test_nach_zehn_minuten_neustart_mit_push_und_bestaetigung():
    alle = ablauf(DIENSTAG, [(0, False), (10, False), (13, True)])
    assert arten(alle[1]) == ["neustart", "QuantDesk: Gateway neu gestartet"]
    assert arten(alle[2]) == ["QuantDesk: Gateway wieder eingeloggt"]
    assert "nach automatischem Neustart" in alle[2][0][2]


def test_hoechstens_zwei_neustarts_dann_alarm_und_keine_endlosschleife():
    schritte = [(0, False), (10, False), (15, False), (17, False), (26, False), (34, False)] + \
               [(40 + i * 10, False) for i in range(20)]
    alle = ablauf(DIENSTAG, schritte)
    neustarts = sum(a.count(("neustart",)) for a in alle)
    alarme = [a for aktionen in alle for a in aktionen if a[0] == "push" and a[3] is True]
    assert neustarts == 2
    assert len(alarme) == 1 and "lässt sich nicht einloggen" in alarme[0][1]
    assert arten(alle[3]) == []  # nach dem 1. Neustart bekommt das Gateway 8 Minuten Zeit


def test_neuer_tag_versucht_es_wieder():
    zustand = waechter.neuer_zustand() | {"tag": "2026-10-27", "aufgegeben": True, "neustarts_folge": 2,
                                           "kaputt_seit": DIENSTAG}
    _, aktionen = waechter.entscheide(zustand, False, ny(2026, 10, 28, 9, 0))
    assert ("neustart",) in aktionen


def test_obergrenze_pro_tag():
    zustand = waechter.neuer_zustand() | {"tag": "2026-10-27", "neustarts_heute": 4, "kaputt_seit": DIENSTAG - 20 * MIN}
    zustand, aktionen = waechter.entscheide(zustand, False, DIENSTAG)
    assert ("neustart",) not in aktionen and zustand["aufgegeben"] is True


def test_in_der_ibkr_wartung_nur_zuschauen():
    samstag, sonntag_frueh, sonntag_spaeter = ny(2026, 10, 24, 14, 0), ny(2026, 10, 25, 7, 30), ny(2026, 10, 25, 9, 0)
    assert waechter.in_wartung(samstag) and waechter.in_wartung(sonntag_frueh)
    assert waechter.in_wartung(ny(2026, 10, 27, 23, 50)) and waechter.in_wartung(ny(2026, 10, 28, 0, 30))
    assert not waechter.in_wartung(sonntag_spaeter) and not waechter.in_wartung(DIENSTAG)
    alle = ablauf(samstag, [(0, False), (60, False), (600, False)])
    assert all(a == [] for a in alle)
    # Sonntag nach der Wartung: war schon lange ausgeloggt -> sofort Neustart
    zustand = waechter.neuer_zustand() | {"kaputt_seit": samstag, "tag": "2026-10-25"}
    _, aktionen = waechter.entscheide(zustand, False, sonntag_spaeter)
    assert ("neustart",) in aktionen


def test_nur_paper_konten_gelten_als_gesund():
    assert waechter.ist_gesund({"konten": ["DUO844164"]})
    assert not waechter.ist_gesund({"konten": None}) and not waechter.ist_gesund({"konten": []})
    assert not waechter.ist_gesund({"konten": ["U1234567"]})  # Echtgeld: nie "gesund" – Neustart hilft da nicht


# ====================================================================== Wächter: ein Durchgang
def _durchgang(tmp_path, monkeypatch, container, ergebnis):
    neustarts, pushes = [], []
    monkeypatch.setattr(waechter, "pruefen", lambda: (container, ergebnis))
    monkeypatch.setattr(waechter, "neu_starten", lambda name: neustarts.append(name) or True)
    monkeypatch.setattr(waechter, "push", lambda env, titel, text, wichtig: pushes.append(titel))
    return neustarts, pushes


def test_durchgang_startet_neu_und_merkt_es_sich(tmp_path, monkeypatch):
    (tmp_path / "state" / "system").mkdir(parents=True)
    pfad = tmp_path / "state" / "system" / "waechter.json"
    pfad.write_text(json.dumps({"kaputt_seit": time.time() - 30 * MIN, "tag": ""}))
    monkeypatch.setattr(waechter, "in_wartung", lambda jetzt: False)
    neustarts, pushes = _durchgang(tmp_path, monkeypatch, {"name": "quantdesk-ib-gateway-1", "zustand": "running",
                                                           "ip": "x"}, {"konten": None, "fehler": "Zeitlimit"})
    waechter.durchgang(str(tmp_path))
    assert neustarts == ["quantdesk-ib-gateway-1"] and pushes == ["QuantDesk: Gateway neu gestartet"]
    gespeichert = json.loads(pfad.read_text())
    assert gespeichert["neustarts_folge"] == 1 and gespeichert["letzte_pruefung"]["gesund"] is False


def test_von_hand_gestoppter_container_wird_nie_gestartet(tmp_path, monkeypatch):
    monkeypatch.setattr(waechter, "in_wartung", lambda jetzt: False)
    neustarts, pushes = _durchgang(tmp_path, monkeypatch, {"name": "gw", "zustand": "exited", "ip": None},
                                   {"konten": None})
    for _ in range(3):
        waechter.durchgang(str(tmp_path))
    assert neustarts == [] and pushes == []


def test_push_verraet_den_token_nie(tmp_path, monkeypatch, caplog):
    (tmp_path / ".env").write_text("NTFY_TOPIC=quantdesk\nNTFY_TOKEN=geheimer-test-wert\n")
    gesendet = []

    def urlopen(anfrage, timeout):
        gesendet.append(anfrage)
        raise OSError("ntfy nicht erreichbar (Test)")

    monkeypatch.setattr(waechter.urllib.request, "urlopen", urlopen)
    with caplog.at_level("INFO"):
        waechter.push(str(tmp_path / ".env"), "Titel", "Text", True)
    assert gesendet and gesendet[0].full_url == "http://127.0.0.1:8090"     # nur lokal, nicht ins Internet
    assert gesendet[0].get_header("Authorization") == "Bearer geheimer-test-wert"
    assert "geheimer-test-wert" not in caplog.text


# ====================================================================== Sammler: hängt nie am Gateway
def _schnelle_messungen(monkeypatch):
    """Alle Host-Befehle durch schnelle Attrappen ersetzen – nur das Gateway hängt."""
    monkeypatch.setattr(messen, "server", lambda: {"kerne": 4})
    monkeypatch.setattr(messen, "tailscale", lambda: {"verbunden": True, "hostname": "quantdesk"})
    monkeypatch.setattr(messen, "container", lambda merker, jetzt: {"ib-gateway": {"zustand": "running", "_ip": "x"}})
    monkeypatch.setattr(messen, "dashboard_antwortet", lambda: True)
    monkeypatch.setattr(messen, "ntfy", lambda pfad, merker: {"erreichbar": True, "letzte_nachricht": None})
    monkeypatch.setattr(messen, "bot_logs", lambda stunden: "")
    monkeypatch.setattr(messen, "sicherheitsupdates", lambda: 0)


def test_haengendes_gateway_haelt_den_sammler_nicht_auf(tmp_path, monkeypatch):
    _schnelle_messungen(monkeypatch)
    nie = threading.Event()
    monkeypatch.setattr(messen, "ib_gateway", lambda ip: nie.wait())  # hängt für immer
    monkeypatch.setitem(messen.FRIST, "ib", 0.5)
    (tmp_path / "bots.toml").write_text("")
    start = time.monotonic()
    ergebnis = sammler.einmal(str(tmp_path), messen.Zwischenspeicher(), {})
    assert time.monotonic() - start < 5
    datei = json.loads((tmp_path / "state" / "system" / "system.json").read_text(encoding="utf-8"))
    assert time.time() - datei["erzeugt"] < 10                                   # frisch, trotz hängendem Gateway
    k = {x["id"]: x for x in ergebnis["kaesten"]}
    assert k["tws-api"]["stufe"] == "rot" and "Zeitlimit" in k["tws-api"]["kurz"]
    assert k["ibkr"]["konto_zustand"] == "unbekannt" and ergebnis["warnungen"] == []
    nie.set()


def test_kaputter_zyklus_schreibt_trotzdem_eine_frische_datei(tmp_path, monkeypatch):
    monkeypatch.setattr(sammler, "sammeln", lambda *a: (_ for _ in ()).throw(KeyError("kaputt")))
    ergebnis = sammler.einmal(str(tmp_path), messen.Zwischenspeicher(), {})
    datei = json.loads((tmp_path / "state" / "system" / "system.json").read_text(encoding="utf-8"))
    assert time.time() - datei["erzeugt"] < 10 and ergebnis["kaesten"][0]["id"] == "sammler"
    assert datei["ampel"]["stufe"] == "rot" and "KeyError" in datei["kaesten"][0]["kurz"]


def test_docker_haengt_heisst_zustand_unbekannt_nicht_container_fehlt(tmp_path, monkeypatch):
    _schnelle_messungen(monkeypatch)
    nie = threading.Event()
    monkeypatch.setattr(messen, "container", lambda merker, jetzt: nie.wait())
    monkeypatch.setitem(messen.FRIST, "container", 0.5)
    monkeypatch.setattr(messen, "ib_gateway", lambda ip: {"port_offen": False, "konten": None, "fehler": "x"})
    (tmp_path / "bots.toml").write_text("")
    k = {x["id"]: x for x in sammler.einmal(str(tmp_path), messen.Zwischenspeicher(), {})["kaesten"]}
    assert k["c-bot"]["stufe"] == "rot" and "Docker antwortet nicht" in k["c-bot"]["kurz"]
    assert "Container fehlt" not in json.dumps(k, ensure_ascii=False)
    nie.set()


def test_langsame_messung_laeuft_im_hintergrund():
    nie = threading.Event()
    speicher = messen.Zwischenspeicher()
    start = time.monotonic()
    assert speicher.hole_im_hintergrund("updates", 1800, nie.wait, "unbekannt") == "unbekannt"
    assert speicher.hole_im_hintergrund("updates", 1800, nie.wait, "unbekannt") == "unbekannt"  # kein zweiter Faden
    assert time.monotonic() - start < 1
    nie.set()


def test_gateway_abfrage_hat_eine_harte_gesamtfrist():
    """Gateway nimmt die Verbindung an und schweigt dann – die Abfrage endet trotzdem nach der Frist."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    offen = []
    threading.Thread(target=lambda: offen.append(server.accept()), daemon=True).start()
    start = time.monotonic()
    ergebnis = tws.konten_abfragen("127.0.0.1", server.getsockname()[1], timeout=1.0)
    assert time.monotonic() - start < 2.5
    assert ergebnis["port_offen"] is True and ergebnis["konten"] is None and "Zeitlimit" in ergebnis["fehler"]
    server.close()


# ====================================================================== Konto-Abzeichen: drei Zustände statt zwei
def test_konto_zustand():
    assert konto_zustand(None, "DUO844164") == "unbekannt"
    assert konto_zustand([], "DUO844164") == "unbekannt"
    assert konto_zustand(["DUO844164"], "DUO844164") == "paper"
    assert konto_zustand(["DUO844164"], None) == "paper"
    assert konto_zustand(["U7654321"], "DUO844164") == "kein_paper"
    assert konto_zustand(["DU1111111"], "DUO844164") == "falsches_konto"


def test_ausgeloggtes_gateway_ist_kein_echtgeld_alarm():
    roh = beispiel()
    roh["ib"].update(konten=None, fehler="Gateway antwortet nicht")
    ergebnis = bewerten(roh)
    ibkr = {k["id"]: k for k in ergebnis["kaesten"]}["ibkr"]
    assert ibkr["konto_zustand"] == "unbekannt" and ibkr["paper"] is False
    assert ergebnis["warnungen"] == []                      # kein roter Balken "kein Paper-Konto"
    roh["ib"]["konten"] = ["U7654321"]
    assert {k["id"]: k for k in bewerten(roh)["kaesten"]}["ibkr"]["konto_zustand"] == "kein_paper"


def test_abzeichen_in_der_app():
    js = (ROOT / "dashboard" / "static" / "kern.js").read_text(encoding="utf-8")
    assert "NICHT PAPER" not in js and "konto_zustand" in js
    for zustand, text in (("paper", "PAPER"), ("kein_paper", "KEIN PAPER-KONTO"), ("unbekannt", "Konto unbekannt")):
        assert f'{zustand}: {{ klasse:' in js and f'text: "{text}"' in js


# ====================================================================== Konfiguration
def test_kaltstart_und_waechter_dienst():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'TWS_COLD_RESTART: "07:05"' in compose and 'AUTO_RESTART_TIME: "11:59 PM"' in compose
    assert "docker.sock" not in compose                     # Dashboard bleibt ohne Docker-Socket
    dienst = (ROOT / "deploy" / "gatewaywaechter.service").read_text(encoding="utf-8")
    for zeile in ("IPAddressDeny=any", "ReadWritePaths=/home/tim/quantdesk/deploy/state/system",
                  "ProtectHome=read-only", "NoNewPrivileges=yes", "python3 -m systemstatus.waechter"):
        assert zeile in dienst
    quelltext = (ROOT / "systemstatus" / "waechter.py").read_text(encoding="utf-8")
    assert "SELL" not in quelltext and "placeOrder" not in quelltext   # der Wächter handelt nie
