"""Systemseite /system: Sammler (systemstatus) und Dashboard-Route. Keine echten Server-Befehle – alles mit Beispieldaten."""
from __future__ import annotations

import ast
import datetime as dt
import json
import socket
import struct
import sys
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from quantdesk.config import load_settings
from quantdesk.notify import Notifier
from systemstatus import formate, messen, tws
from systemstatus.bewerten import (
    bewerten, entschaerfen, faelliger_lauf, maskiere_konto, naechster_lauf, port22_offen, unix_zeit,
)

ROOT = Path(__file__).resolve().parent.parent
NY = ZoneInfo("America/New_York")
C_EINTRAG = {"id": "c", "name": "Strategie C", "strategie": "Grid", "eingerichtet": True, "waehrung": "USD", "container": "bot",
             "zeitzone": "America/New_York", "laeufe": ["10:00", "15:30"]}


def ny(*args) -> float:
    return dt.datetime(*args, tzinfo=NY).timestamp()


def beispiel(jetzt: float | None = None) -> dict:
    """Rohdaten eines gesunden Systems: Donnerstag 08.10.2026, 12:00 New York, Lauf um 10:00 war ok."""
    jetzt = jetzt or ny(2026, 10, 8, 12, 0)
    return {
        "jetzt": jetzt,
        "server": {"last": [0.3, 0.25, 0.2], "kerne": 6, "ram_gesamt": 12e9, "ram_frei": 9e9,
                   "platte_gesamt": 100e9, "platte_frei": 60e9, "uptime": 3 * 86400, "systemzeit": "08.10.2026 16:00:00 UTC",
                   "zeitzone": "Etc/UTC", "ufw_aktiv": True,
                   "ufw_regeln": ["Anywhere on tailscale0     ALLOW       Anywhere",
                                  "41641/udp                  ALLOW       Anywhere"],
                   "sicherheitsupdates": 0},
        "tailscale": {"verbunden": True, "hostname": "quantdesk", "letzter_handshake": jetzt - 30,
                      "geraete": [{"art": "iPhone", "online": True, "zuletzt": jetzt},
                                  {"art": "Laptop", "online": False, "zuletzt": jetzt - 3600}],
                      "zertifikat_ablauf": jetzt + 60 * 86400},
        "container": {name: {"zustand": "running", "gestartet": jetzt - 86400, "neustarts_24h": 0, "neustarts_gesamt": 0,
                             "image_tag": "latest"} for name in ("bot", "ib-gateway", "ntfy", "dashboard")},
        "dashboard_antwortet": True,
        "ntfy": {"erreichbar": True, "letzte_nachricht": jetzt - 7200},
        "ib": {"port_offen": True, "konten": ["DUO844164"], "fehler": "", "geprueft": jetzt - 60, "erwartet": "DUO844164"},
        "bots": [
            {"eintrag": C_EINTRAG, "fehler": "",
             "status": {"mode": "PAPER", "c_account": {"invested": 12345.6, "positions": {"AAPL": {}, "KO": {}}}},
             "laeufe": [{"ts": ny(2026, 10, 8, 10, 0, 1), "mode": "trade", "ok": True, "errors": []}]},
            {"eintrag": {"id": "lotse", "name": "Lotse", "eingerichtet": False, "container": "lotse", "format": "lotse-tagebuch",
                       "laeufe": ["10:30"], "zeitzone": "Europe/Zurich", "waehrung": "CHF"}, "status": None, "laeufe": [], "fehler": ""},
        ],
        "backup": {"eingerichtet": False, "letzter_snapshot": None},
        "totmann": {"eingerichtet": False, "ping_fehler": None, "letzter_versuch": None},
    }


def kaesten(roh: dict) -> dict:
    return {k["id"]: k for k in bewerten(roh)["kaesten"]}


# ====================================================================== Gesamtbild
def test_gesundes_system_ist_gruen_und_fehlendes_grau():
    k = kaesten(beispiel())
    grau = {i for i, x in k.items() if x["stufe"] == "grau"}
    assert grau == {"laptop", "bot-lotse", "c-lotse", "backup", "totmann"}  # Laptop offline ist kein Fehler
    assert all(x["stufe"] == "gruen" for i, x in k.items() if i not in grau), {i: x["gruende"] for i, x in k.items()}
    assert k["backup"]["kurz"] == "noch nicht eingerichtet" and k["totmann"]["kurz"] == "noch nicht eingerichtet"
    ergebnis = bewerten(beispiel())
    assert ergebnis["warnungen"] == [] and ergebnis["zaehler"]["rot"] == 0
    json.dumps(ergebnis, allow_nan=False)  # gültiges JSON


# ====================================================================== die wichtigste Prüfung: Paper-Konto
def test_kein_paper_konto_ist_rot_mit_klartext_und_warnung_oben():
    roh = beispiel()
    roh["ib"]["konten"] = ["U7654321"]
    ergebnis = bewerten(roh)
    ibkr = {k["id"]: k for k in ergebnis["kaesten"]}["ibkr"]
    assert ibkr["stufe"] == "rot" and ibkr["kurz"] == "kein Paper-Konto"
    assert ergebnis["warnungen"] and "kein Paper-Konto" in ergebnis["warnungen"][0]
    assert "U7654321" not in json.dumps(ergebnis) and "U…321" in json.dumps(ergebnis, ensure_ascii=False)


def test_paper_konto_wird_maskiert_aber_auf_du_geprueft():
    text = json.dumps(bewerten(beispiel()), ensure_ascii=False)
    assert "DUO844164" not in text and "DU…164" in text
    assert maskiere_konto("DUO844164") == "DU…164" and maskiere_konto("U1234567") == "U…567"


def test_gateway_nicht_eingeloggt_und_port_zu():
    roh = beispiel()
    roh["ib"].update(konten=None, fehler="Gateway antwortet nicht")
    k = kaesten(roh)
    assert k["tws-api"]["stufe"] == "rot" and "nicht eingeloggt" in k["tws-api"]["kurz"]
    assert k["ibkr"]["stufe"] == "rot"
    roh["ib"].update(port_offen=False)
    assert kaesten(roh)["tws-api"]["kurz"] == "Port 4004 zu"


def test_alte_ib_pruefung_ist_nicht_gruen():
    roh = beispiel()
    roh["ib"]["geprueft"] = roh["jetzt"] - 3600
    assert kaesten(roh)["ibkr"]["stufe"] == "gelb"


# ====================================================================== Server
@pytest.mark.parametrize("frei,stufe", [(60e9, "gruen"), (15e9, "gelb"), (4e9, "rot")])
def test_platte_schwellen(frei, stufe):
    roh = beispiel()
    roh["server"]["platte_frei"] = frei
    assert kaesten(roh)["server"]["stufe"] == stufe


def test_firewall_aus_und_port_22_offen_sind_rot():
    roh = beispiel()
    roh["server"]["ufw_aktiv"] = False
    assert kaesten(roh)["server"]["stufe"] == "rot"
    roh = beispiel()
    roh["server"]["ufw_regeln"].append("22/tcp                     ALLOW       Anywhere")
    server = kaesten(roh)["server"]
    assert server["stufe"] == "rot" and "Port 22 ist offen" in server["gruende"]


@pytest.mark.parametrize("regel,offen", [
    ("22/tcp                     ALLOW       Anywhere", True),
    ("22 (v6)                    ALLOW       Anywhere (v6)", True),
    ("OpenSSH                    ALLOW       Anywhere", True),
    ("22/tcp                     LIMIT       Anywhere", True),
    ("Anywhere                   ALLOW       203.0.113.5", True),
    ("Anywhere on tailscale0     ALLOW       Anywhere", False),
    ("22/tcp on tailscale0       ALLOW       Anywhere", False),
    ("41641/udp                  ALLOW       Anywhere", False),
    ("2222/tcp                   ALLOW       Anywhere", False),
])
def test_port22_aus_ufw_regeln(regel, offen):
    assert port22_offen([regel]) is offen


def test_zeitzone_muss_utc_sein_und_last():
    roh = beispiel()
    roh["server"].update(zeitzone="America/New_York", last=[7.0, 7.5, 6.0])
    server = kaesten(roh)["server"]
    assert server["stufe"] == "gelb" and len(server["gruende"]) == 2


# ====================================================================== Container, Tailscale, ntfy
def test_container_neustarts_zustand_und_fehlend():
    roh = beispiel()
    roh["container"]["ib-gateway"]["neustarts_24h"] = 3
    roh["container"]["ntfy"]["zustand"] = "exited"
    del roh["container"]["bot"]
    k = kaesten(roh)
    assert k["c-ib-gateway"]["stufe"] == "gelb" and k["c-ntfy"]["stufe"] == "rot" and k["c-bot"]["stufe"] == "rot"
    assert k["tws-api"]["stufe"] == "gruen"  # Gateway läuft (nur oft neu gestartet)


def test_neustarts_zaehlt_nur_echte_neustarts_der_letzten_24h():
    merker = {}
    assert messen.neustarts_zaehlen(merker, "ib-gateway", "a", 5, 1000.0) == 0   # Vorgeschichte zählt nicht
    assert messen.neustarts_zaehlen(merker, "ib-gateway", "a", 7, 2000.0) == 2
    assert messen.neustarts_zaehlen(merker, "ib-gateway", "a", 7, 2000.0 + 86400) == 0  # älter als 24 h
    assert messen.neustarts_zaehlen(merker, "ib-gateway", "b", 0, 90000.0) == 0  # neu erstellt (Update)


def test_dashboard_antwortet_nicht_ist_rot():
    roh = beispiel()
    roh["dashboard_antwortet"] = False
    assert kaesten(roh)["c-dashboard"]["stufe"] == "rot"


def test_tailscale_und_zertifikat():
    roh = beispiel()
    roh["tailscale"]["zertifikat_ablauf"] = roh["jetzt"] + 5 * 86400
    assert kaesten(roh)["tailscale"]["stufe"] == "gelb"
    roh["tailscale"]["verbunden"] = False
    assert kaesten(roh)["tailscale"]["stufe"] == "rot"


def test_ntfy_still_seit_einer_woche_ist_gelb():
    roh = beispiel()
    roh["ntfy"]["letzte_nachricht"] = roh["jetzt"] - 8 * 86400
    assert kaesten(roh)["c-ntfy"]["stufe"] == "gelb"
    roh["ntfy"]["erreichbar"] = False
    assert kaesten(roh)["c-ntfy"]["stufe"] == "rot"


# ====================================================================== Bots
@pytest.mark.parametrize("kaputt", [float("nan"), float("inf"), 1.8e308])
def test_bot_mit_nan_inf_ist_rot_und_nichts_stuerzt_ab(kaputt):
    roh = beispiel()
    roh["bots"][0]["status"]["c_account"]["invested"] = kaputt
    ergebnis = bewerten(roh)
    bot = {k["id"]: k for k in ergebnis["kaesten"]}["bot-c"]
    assert bot["stufe"] == "rot" and "nan/inf" in bot["kurz"] and "c_account.invested" in bot["kurz"]
    json.dumps(ergebnis, allow_nan=False)


def test_bot_lauf_gescheitert_oder_mit_fehlern():
    roh = beispiel()
    roh["bots"][0]["laeufe"][-1].update(ok=False, errors=["IB Gateway nicht erreichbar"])
    bot = kaesten(roh)["bot-c"]
    assert bot["stufe"] == "rot" and "IB Gateway nicht erreichbar" in bot["kurz"]
    roh["bots"][0]["laeufe"][-1].update(ok=True, errors=["Speicher 86 % belegt."])
    assert kaesten(roh)["bot-c"]["stufe"] == "gelb"


def test_bot_modus_als_abzeichen_und_zahlen():
    bot = kaesten(beispiel())["bot-c"]
    assert bot["modus"] == "PAPER"
    werte = {z["t"]: z.get("w") for z in bot["zeilen"]}
    assert werte["Investiert"] == "12'346 USD" and werte["Offene Positionen"] == "2"
    assert any(z["t"] == "Letzter Lauf" and z["zone"] == "America/New_York" for z in bot["zeilen"])


def test_verpasster_lauf_wird_gelb_aber_nicht_am_wochenende():
    lauf_fr = {"ts": ny(2026, 10, 9, 15, 30, 2), "mode": "reconcile", "ok": True, "errors": []}
    montag_frueh = beispiel(ny(2026, 10, 12, 8, 0))   # Fr 15:30 ist der letzte fällige Lauf
    montag_frueh["bots"][0]["laeufe"] = [lauf_fr]
    assert kaesten(montag_frueh)["bot-c"]["stufe"] == "gruen"
    montag_mittag = beispiel(ny(2026, 10, 12, 11, 0))  # 10:00 hätte laufen müssen
    montag_mittag["bots"][0]["laeufe"] = [lauf_fr]
    assert kaesten(montag_mittag)["bot-c"]["gruende"] == ["geplanter Lauf fehlt"]


def test_zeitplan():
    freitag_abend = ny(2026, 10, 9, 16, 0)
    assert naechster_lauf(freitag_abend, ["10:00", "15:30"], "America/New_York") == ny(2026, 10, 12, 10, 0)
    assert faelliger_lauf(ny(2026, 10, 9, 10, 30), ["10:00", "15:30"], "America/New_York") == ny(2026, 10, 8, 15, 30)
    assert faelliger_lauf(ny(2026, 10, 9, 10, 50), ["10:00", "15:30"], "America/New_York") == ny(2026, 10, 9, 10, 0)


def test_zeit_ohne_zeitzone_wird_nicht_geraten():
    assert unix_zeit("2026-10-08T10:00:00") is None
    assert unix_zeit("2026-10-08T10:00:00+02:00") == dt.datetime(2026, 10, 8, 8, tzinfo=dt.timezone.utc).timestamp()
    assert unix_zeit(1760000000) == 1760000000.0


def test_registry_im_repo_c_und_lotse():
    eintraege = messen.registry(str(ROOT / "deploy" / "bots.toml"))
    nach_id = {e["id"]: e for e in eintraege}
    assert nach_id["c"]["status_datei"] == "state/data/status.json" and nach_id["c"]["container"] == "bot"
    assert nach_id["lotse"]["eingerichtet"] is True and nach_id["lotse"]["format"] == "lotse-tagebuch"
    assert nach_id["lotse"]["container"] == "lotse" and nach_id["lotse"]["laeufe_datei"] == "state-lotse/laeufe.jsonl"
    roh = beispiel()
    roh["bots"] = [messen.bot("gibt-es-nicht", e) for e in eintraege]
    k = kaesten(roh)
    assert k["bot-c"]["stufe"] == "rot" and k["bot-c"]["kurz"] == "Statusdatei fehlt"
    assert k["bot-lotse"]["stufe"] == "gelb" and k["bot-lotse"]["kurz"] == "noch kein Lauf im Protokoll"
    assert k["c-lotse"]["stufe"] == "rot"  # Lotse eingerichtet, aber kein Container -> fehlt


# ====================================================================== Lotse: anderes Protokoll-Format
ZH = ZoneInfo("Europe/Zurich")


def lotse_zeilen(nummer: int, zeit: str, ok: bool = True, ende: bool = True, modus: str = "VORSCHLAG") -> list[dict]:
    """Wie Lotses Tagebuch: start, (kurse), bei Erfolg kasse, dann ende. Zeiten ohne Zeitzone, wie Lotse sie schreibt."""
    out = [{"lauf": nummer, "ereignis": "start", "zeit": zeit, "modus": modus, "zieldatei": "..."},
           {"lauf": nummer, "ereignis": "kurse", "zeit": zeit, "kurse": {}}]
    if ok:
        out.append({"lauf": nummer, "ereignis": "kasse", "zeit": zeit, "cash": 250.0, "investiert": 51.5})
    if ende:
        out.append({"lauf": nummer, "ereignis": "ende", "zeit": zeit,
                    "ergebnis": "Nichts zu tun." if ok else "Broker: IB Gateway nicht erreichbar", "gesamt": None})
    return out


def test_lotse_tagebuch_uebersetzt_start_kasse_ende():
    jetzt = dt.datetime(2026, 10, 8, 12, 0, tzinfo=ZH).timestamp()
    zeilen = lotse_zeilen(1, "2026-10-07T04:30:01", ok=True) + lotse_zeilen(2, "2026-10-08T04:30:02", ok=False)
    kauf = [{"papier": "SSAC", "seite": "KAUF", "menge": 0.5}, {"papier": "CSBGC7", "seite": "KAUF", "menge": 2},
            {"papier": "CSBGC7", "seite": "VERKAUF", "menge": 2}]
    status, laeufe = formate.lotse_tagebuch(zeilen, kauf, [], jetzt, "America/New_York", 25)
    assert (status["modus"], status["investiert"], status["positionen"]) == ("VORSCHLAG", 51.5, 1)
    assert [r["ok"] for r in laeufe] == [True, False]
    assert laeufe[-1]["errors"] == ["Broker: IB Gateway nicht erreichbar"]
    # 04:30 New York (ohne Zeitzone geschrieben) = 10:30 Zürich
    assert laeufe[-1]["ts"] == dt.datetime(2026, 10, 8, 10, 30, 2, tzinfo=ZH).timestamp()


def test_lotse_lauf_ohne_ende_laeuft_noch_oder_ist_abgestuerzt():
    start = dt.datetime(2026, 10, 8, 10, 30, tzinfo=ZH)
    zeilen = lotse_zeilen(1, "2026-10-07T04:30:00") + lotse_zeilen(2, "2026-10-08T04:30:00", ok=False, ende=False)
    _, laeufe = formate.lotse_tagebuch(zeilen, [], [], (start + dt.timedelta(minutes=5)).timestamp(), "America/New_York", 25)
    assert [r["lauf"] for r in laeufe] == [1]  # Lauf 2 läuft noch – zählt noch nicht
    _, laeufe = formate.lotse_tagebuch(zeilen, [], [], (start + dt.timedelta(hours=1)).timestamp(), "America/New_York", 25)
    assert laeufe[-1]["ok"] is False and "ohne Ende" in laeufe[-1]["errors"][0]


def test_lotse_zeit_ohne_zone_wird_ohne_angabe_nicht_geraten():
    _, laeufe = formate.lotse_tagebuch(lotse_zeilen(1, "2026-10-08T04:30:00"), [], [], 2e9, None, 25)
    assert laeufe[0]["ts"] is None


def _lotse_ordner(tmp_path, zeilen, ausfuehrungen=()):
    (tmp_path / "state-lotse").mkdir(parents=True)
    (tmp_path / "state-lotse" / "laeufe.jsonl").write_text("".join(json.dumps(z) + "\n" for z in zeilen), encoding="utf-8")
    if ausfuehrungen:
        (tmp_path / "state-lotse" / "ausfuehrungen.jsonl").write_text(
            "".join(json.dumps(a) + "\n" for a in ausfuehrungen), encoding="utf-8")
    eintrag = {e["id"]: e for e in messen.registry(str(ROOT / "deploy" / "bots.toml"))}["lotse"]
    return eintrag


def test_lotse_auf_system_gruen_gelb_rot(tmp_path):
    """Mit dem echten Registry-Eintrag aus deploy/bots.toml und Dateien wie von Lotse geschrieben."""
    eintrag = _lotse_ordner(tmp_path, lotse_zeilen(1, "2026-10-08T04:30:01"),
                            [{"papier": "SSAC", "seite": "KAUF", "menge": 0.25}])
    jetzt = dt.datetime(2026, 10, 8, 12, 0, tzinfo=ZH).timestamp()
    roh = beispiel(jetzt)
    roh["container"]["lotse"] = dict(roh["container"]["bot"])
    roh["bots"][1] = messen.bot(str(tmp_path), eintrag, jetzt)
    k = kaesten(roh)
    assert k["bot-lotse"]["stufe"] == "gruen", k["bot-lotse"]["gruende"]
    assert k["bot-lotse"]["modus"] == "VORSCHLAG" and k["bot-lotse"]["kurz"] == "52 CHF · 1 Pos."
    assert k["bot-lotse"]["container"] == "lotse" and k["c-lotse"]["stufe"] == "gruen"
    # nächster Tag 12:00 Zürich, der 10:30-Lauf fehlt -> gelb
    roh["jetzt"] = dt.datetime(2026, 10, 9, 12, 0, tzinfo=ZH).timestamp()
    assert kaesten(roh)["bot-lotse"]["gruende"] == ["geplanter Lauf fehlt"]


def test_lotse_gescheiterter_lauf_ist_rot(tmp_path):
    eintrag = _lotse_ordner(tmp_path, lotse_zeilen(1, "2026-10-08T04:30:01", ok=False))
    jetzt = dt.datetime(2026, 10, 8, 12, 0, tzinfo=ZH).timestamp()
    roh = beispiel(jetzt)
    roh["bots"][1] = messen.bot(str(tmp_path), eintrag, jetzt)
    lotse = kaesten(roh)["bot-lotse"]
    assert lotse["stufe"] == "rot" and "IB Gateway nicht erreichbar" in lotse["kurz"]


def test_lotse_nicht_eingerichtet_ohne_container_ist_grau():
    k = kaesten(beispiel())
    assert k["bot-lotse"]["stufe"] == "grau" and k["c-lotse"]["stufe"] == "grau"


def test_skizze_kennt_container_aus_der_registry():
    js = (ROOT / "dashboard" / "static" / "system.js").read_text(encoding="utf-8")
    assert "bot.container" in js and "p._linien" in js and '"c-bot"' not in js  # kein fest verdrahteter Bot-Container


def test_dritter_bot_braucht_nur_einen_registry_eintrag(tmp_path):
    """Andere Feldnamen, ISO-Zeiten mit Zeitzone – alles nur über die Registry, ohne Codeänderung."""
    (tmp_path / "bots.toml").write_text('''
[[bot]]
id = "drei"
name = "Bot Drei"
strategie = "Test"
eingerichtet = true
status_datei = "drei/stand.json"
laeufe_datei = "drei/laeufe.jsonl"
modus_feld = "modus"
investiert_feld = "konto.investiert"
positionen_feld = "konto.anzahl"
lauf_zeit_feld = "zeit"
lauf_ok_feld = "erfolg"
lauf_fehler_feld = "fehler"
waehrung = "CHF"
zeitzone = "Europe/Zurich"
laeufe = ["09:30"]
''', encoding="utf-8")
    (tmp_path / "drei").mkdir()
    (tmp_path / "drei" / "stand.json").write_text(json.dumps({"modus": "VORSCHLAG", "konto": {"investiert": 500, "anzahl": 3}}))
    (tmp_path / "drei" / "laeufe.jsonl").write_text('kaputte Zeile\n' + json.dumps(
        {"zeit": "2026-10-08T09:30:05+02:00", "erfolg": True, "fehler": []}) + "\n", encoding="utf-8")
    roh = beispiel(dt.datetime(2026, 10, 8, 12, 0, tzinfo=ZoneInfo("Europe/Zurich")).timestamp())
    roh["bots"] = [messen.bot(str(tmp_path), e) for e in messen.registry(str(tmp_path / "bots.toml"))]
    bot = kaesten(roh)["bot-drei"]
    assert bot["stufe"] == "gruen", bot["gruende"]
    assert bot["modus"] == "VORSCHLAG" and bot["kurz"] == "500 CHF · 3 Pos."


# ====================================================================== Backup, Totmannschalter
def test_backup_schwellen_und_snapshot_aus_log(tmp_path):
    env = tmp_path / ".env"
    env.write_text("RESTIC_REPOSITORY=b2:eimer:quantdesk\nHEALTHCHECKS_URL=\n", encoding="utf-8")
    logs = ("2026-10-07T19:31:02.123456789Z Files: 12 new\n"
            "2026-10-07T19:31:05.000000001Z snapshot 1a2b3c4d saved\n")
    bk = messen.backup(str(env), logs)
    assert bk["eingerichtet"] is True and bk["letzter_snapshot"] == dt.datetime(2026, 10, 7, 19, 31, 5, tzinfo=dt.timezone.utc).timestamp()
    assert messen.totmann(str(env), logs, [])["eingerichtet"] is False
    roh = beispiel()
    for alter, stufe in ((3600, "gruen"), (3 * 86400, "gelb"), (8 * 86400, "rot")):
        roh["backup"] = {"eingerichtet": True, "letzter_snapshot": roh["jetzt"] - alter}
        assert kaesten(roh)["backup"]["stufe"] == stufe
    roh["backup"] = {"eingerichtet": True, "letzter_snapshot": None}
    assert kaesten(roh)["backup"]["stufe"] == "gelb"


def test_totmann_eingerichtet():
    roh = beispiel()
    roh["totmann"] = {"eingerichtet": True, "ping_fehler": False, "letzter_versuch": roh["jetzt"] - 7000}
    assert kaesten(roh)["totmann"]["stufe"] == "gruen"
    roh["totmann"]["ping_fehler"] = True
    assert kaesten(roh)["totmann"]["stufe"] == "gelb"


def test_env_wert_wird_nie_zurueckgegeben(tmp_path):
    env = tmp_path / ".env"
    env.write_text('HEALTHCHECKS_URL="https://hc-ping.com/abc"\nRESTIC_REPOSITORY=\n', encoding="utf-8")
    assert messen.env_gesetzt(str(env), "HEALTHCHECKS_URL") is True
    assert messen.env_gesetzt(str(env), "RESTIC_REPOSITORY") is False
    assert messen.env_gesetzt(str(tmp_path / "fehlt"), "X") is False


# ====================================================================== keine Geheimnisse
def test_entschaerfen_maskiert_ips_pfade_tokens_konten():
    text = entschaerfen("Fehler bei 203.0.113.7 in /home/tim/quantdesk/deploy/.env, tk_abc123, "
                        "quantdesk.tail1234.ts.net, Konto DU1234567, https://hc-ping.com/1234-5678")
    for geheim in ("203.0.113.7", "/home/tim", "tk_abc123", "tail1234", "DU1234567", "1234-5678"):
        assert geheim not in text
    assert "DU…567" in text


def test_bot_fehlertext_mit_geheimnis_wird_maskiert():
    roh = beispiel()
    roh["bots"][0]["laeufe"][-1].update(ok=False, errors=["Verbindung zu 172.18.0.3:4004 abgelehnt, Konto U9988776"])
    text = json.dumps(bewerten(roh), ensure_ascii=False)
    assert "172.18.0.3" not in text and "U9988776" not in text


# ====================================================================== Messungen mit vorgetäuschten Programmen
def test_container_aus_docker_inspect(monkeypatch):
    inspect = [{"Id": "abc", "RestartCount": 1, "Config": {"Image": "ghcr.io/gnzsnz/ib-gateway:stable",
                "Labels": {"com.docker.compose.service": "ib-gateway"}},
                "State": {"Status": "running", "StartedAt": "2026-10-08T10:00:00.123456789Z"},
                "NetworkSettings": {"Networks": {"quantdesk_default": {"IPAddress": "172.18.0.3"}}}}]
    antworten = {"ps": "quantdesk-ib-gateway-1\n", "inspect": json.dumps(inspect)}
    monkeypatch.setattr(messen, "befehl", lambda args, **kw: antworten[args[1]])
    c = messen.container({}, 1e9)["ib-gateway"]
    assert c["zustand"] == "running" and c["image_tag"] == "stable" and c["_ip"] == "172.18.0.3"
    assert c["gestartet"] == dt.datetime(2026, 10, 8, 10, 0, 0, 123456, tzinfo=dt.timezone.utc).timestamp()


def test_tailscale_aus_status_json(monkeypatch):
    status = {"BackendState": "Running", "Self": {"HostName": "quantdesk", "Online": True, "DNSName": "quantdesk.x.ts.net.",
                                                   "TailscaleIPs": ["100.64.0.1"]},
              "Peer": {"a": {"OS": "iOS", "Online": True, "LastHandshake": "2026-10-08T15:59:00Z", "LastSeen": "0001-01-01T00:00:00Z"},
                       "b": {"OS": "windows", "Online": False, "LastHandshake": "0001-01-01T00:00:00Z",
                             "LastSeen": "2026-10-08T12:00:00Z"}}}
    monkeypatch.setattr(messen, "befehl", lambda args, **kw: json.dumps(status))
    t = messen.tailscale()
    assert t["verbunden"] is True and t["hostname"] == "quantdesk"
    assert {g["art"]: g["online"] for g in t["geraete"]} == {"iPhone": True, "Laptop": False}
    assert t["letzter_handshake"] == dt.datetime(2026, 10, 8, 15, 59, tzinfo=dt.timezone.utc).timestamp()


def test_zwischenspeicher_misst_langsames_selten():
    uhr = [0.0]
    zaehler = []
    speicher = messen.Zwischenspeicher(lambda: uhr[0])
    for t in (0, 10, 59, 61):
        uhr[0] = t
        speicher.hole("x", 60, lambda: zaehler.append(1) or len(zaehler))
    assert len(zaehler) == 2


# ====================================================================== TWS-API gegen ein vorgetäuschtes Gateway
def _falsches_gateway(antwort: str):
    """Kleiner Server, der sich wie IB Gateway verhält: Begrüssung, dann nextValidId und managedAccounts."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    empfangen = {}

    def bedienen():
        verbindung, _ = server.accept()
        with verbindung:
            assert verbindung.recv(4) == b"API\0"
            laenge = struct.unpack(">I", verbindung.recv(4))[0]
            empfangen["versionen"] = verbindung.recv(laenge)
            if antwort == "auflegen":
                return
            verbindung.sendall(tws.nachricht(["176", "20261008 12:00:00 EST"]))
            laenge = struct.unpack(">I", verbindung.recv(4))[0]
            empfangen["start"] = verbindung.recv(laenge)
            verbindung.sendall(tws.nachricht(["9", "1", "1"]) + tws.nachricht(["15", "1", antwort]))

    threading.Thread(target=bedienen, daemon=True).start()
    return server, empfangen


def test_tws_konten_abfragen():
    server, empfangen = _falsches_gateway("DUO844164,")
    with server:
        ergebnis = tws.konten_abfragen("127.0.0.1", server.getsockname()[1], timeout=3)
    assert ergebnis == {"port_offen": True, "konten": ["DUO844164"], "fehler": ""}
    assert empfangen["versionen"] == b"v100..176"
    assert empfangen["start"] == b"71\x002\x0091\x00\x00"  # startApi mit eigener Client-ID 91 (Bot C hat 17)


def test_tws_port_offen_aber_gateway_stumm():
    server, _ = _falsches_gateway("auflegen")
    with server:
        ergebnis = tws.konten_abfragen("127.0.0.1", server.getsockname()[1], timeout=3)
    assert ergebnis["port_offen"] is True and ergebnis["konten"] is None


def test_tws_port_zu():
    frei = socket.socket()
    frei.bind(("127.0.0.1", 0))
    port = frei.getsockname()[1]
    frei.close()
    assert tws.konten_abfragen("127.0.0.1", port, timeout=1)["port_offen"] is False


# ====================================================================== Dashboard-Route /system
def _client(tmp_path, monkeypatch, datei: Path) -> TestClient:
    monkeypatch.setenv("QUANTDESK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUANTDESK_MODE", "DRY_RUN")
    monkeypatch.setenv("IBKR_ACCOUNT_ID", "")
    monkeypatch.setenv("DASHBOARD_PIN", "")
    monkeypatch.setenv("QUANTDESK_SYSTEM_FILE", str(datei))
    return TestClient(create_app(load_settings(), Notifier()))


def test_dashboard_system_liest_nur_die_datei(tmp_path, monkeypatch):
    datei = tmp_path / "system" / "system.json"
    client = _client(tmp_path, monkeypatch, datei)
    assert client.get("/api/system").json()["fehlt"] is True
    datei.parent.mkdir()
    datei.write_text(json.dumps(bewerten(beispiel()), ensure_ascii=False), encoding="utf-8")
    daten = client.get("/api/system").json()
    assert daten["kaesten"] and daten["erzeugt"]
    datei.write_text('{"erzeugt": NaN, "kaesten": [{"id": "x", "wert": Infinity}]}', encoding="utf-8")
    r = client.get("/api/system")
    assert r.status_code == 200 and r.json()["erzeugt"] is None and r.json()["kaesten"][0]["wert"] is None
    datei.write_text('{"erzeugt": 1', encoding="utf-8")  # halb geschrieben
    assert client.get("/api/system").json()["fehlt"] is True
    seite = client.get("/system")
    assert seite.status_code == 200 and "system.js" in seite.text
    assert client.get("/static/system.js").status_code == 200 and 'href="#system"' in client.get("/").text


NEUE_SKRIPTE = ("kern.js", "uebersicht.js", "bots.js", "system.js")


def test_app_hat_keine_neuen_aktionen(tmp_path, monkeypatch):
    """Nur anschauen. Die einzige Aktion der ganzen App ist die alte Notbremse (STOP-ALL) von Strategie C."""
    app = _client(tmp_path, monkeypatch, tmp_path / "x.json").app
    schreibend = [r.path for r in app.routes if getattr(r, "methods", set()) - {"GET", "HEAD"}]
    assert schreibend == ["/api/stop"]
    for name in NEUE_SKRIPTE:
        js = (ROOT / "dashboard" / "static" / name).read_text(encoding="utf-8")
        assert "POST" not in js and "method" not in js, name
        assert ".innerHTML =" not in js.replace('innerHTML = ""', ""), name  # Daten nie als HTML
    html = (ROOT / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
    assert html.count("<form") == 1 and 'id="stop-form"' in html and html.count("<button") == 1


def test_eine_app_drei_reiter_und_offline_huelle(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, tmp_path / "x.json")
    html = client.get("/").text
    for reiter in ("#uebersicht", "#bots", "#system"):
        assert f'href="{reiter}"' in html
    assert client.get("/system").text == html  # alte Adresse öffnet dieselbe App
    assert "System ›" not in html and "‹ Dashboard" not in html  # die alten Textlinks sind weg
    sw = (ROOT / "dashboard" / "static" / "sw.js").read_text(encoding="utf-8")
    import re
    for adresse in re.findall(r'(?:src|href)="(/static/[^"]+)"', html):
        assert f'"{adresse}"' in sw, f"{adresse} fehlt in sw.js (SHELL)"
        assert client.get(adresse).status_code == 200, adresse
    manifest = client.get("/manifest.webmanifest").json()
    assert {i["sizes"] for i in manifest["icons"]} >= {"180x180", "192x192", "512x512"}


# ====================================================================== Aufbau: kein Socket, nur lesend, nur Standardbibliothek
def test_dashboard_container_ohne_docker_socket_und_nur_lesend():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    dashboard = compose.split("\n  dashboard:")[1].split("\nsecrets:")[0]
    assert "docker.sock" not in compose
    assert "./state/system:/state/system:ro" in dashboard and "QUANTDESK_SYSTEM_FILE" in dashboard


def test_sammler_dienst_ist_eingezaeunt():
    dienst = (ROOT / "deploy" / "systemstatus.service").read_text(encoding="utf-8")
    for zeile in ("IPAddressDeny=any", "ProtectHome=read-only", "ReadWritePaths=/home/tim/quantdesk/deploy/state/system",
                  "NoNewPrivileges=yes", "python3 -m systemstatus"):
        assert zeile in dienst
    assert "state/system" in (ROOT / "deploy" / "prepare.sh").read_text(encoding="utf-8")


def test_sammler_braucht_nur_die_standardbibliothek():
    for datei in (ROOT / "systemstatus").glob("*.py"):
        for knoten in ast.walk(ast.parse(datei.read_text(encoding="utf-8"))):
            namen = [a.name for a in knoten.names] if isinstance(knoten, ast.Import) else \
                [knoten.module] if isinstance(knoten, ast.ImportFrom) and knoten.module else []
            for name in namen:
                wurzel = name.split(".")[0]
                assert wurzel in sys.stdlib_module_names or wurzel in ("systemstatus", "__future__"), f"{datei.name}: {name}"


# ====================================================================== Auftrag 2: genaues Konto, Ampel, Lotse vollständig
def test_konto_wird_gegen_die_genaue_ibkr_account_id_geprueft():
    roh = beispiel()
    roh["ib"]["erwartet"] = "DU1111111"   # anderes Paper-Konto als gemeldet
    ergebnis = bewerten(roh)
    ibkr = {k["id"]: k for k in ergebnis["kaesten"]}["ibkr"]
    assert ibkr["stufe"] == "rot" and ibkr["kurz"] == "anderes Konto als in .env" and ibkr["paper"] is False
    assert "erwartet ist DU…111" in ergebnis["warnungen"][0]
    assert "DU1111111" not in json.dumps(ergebnis)
    roh["ib"]["erwartet"] = None          # nicht gesetzt: nur DU geprüft -> gelb, nie grün
    assert kaesten(roh)["ibkr"]["stufe"] == "gelb"
    roh["ib"]["erwartet"] = "DUO844164"
    assert kaesten(roh)["ibkr"]["paper"] is True


def test_erwartetes_konto_aus_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TWS_USERID=x\nIBKR_ACCOUNT_ID=duo844164\n", encoding="utf-8")
    assert messen.erwartetes_konto(str(env)) == "DUO844164"
    env.write_text("IBKR_ACCOUNT_ID=\n", encoding="utf-8")
    assert messen.erwartetes_konto(str(env)) is None


def test_ampel_ein_satz_schlechteste_farbe_bestimmt():
    assert bewerten(beispiel())["ampel"] == {"stufe": "gruen", "satz": "Alles läuft", "was": []}
    roh = beispiel()
    roh["ntfy"]["letzte_nachricht"] = None
    a = bewerten(roh)["ampel"]
    assert a["stufe"] == "gelb" and a["satz"] == "1 Sache stimmt nicht" and a["was"] == ["ntfy: keine Nachricht bekannt"]
    roh["server"]["ufw_aktiv"] = False
    a = bewerten(roh)["ampel"]
    assert a["stufe"] == "rot" and a["satz"] == "Etwas ist aus" and a["was"][0] == "Server quantdesk: Firewall (ufw) ist aus"


def test_ampel_grau_zaehlt_nicht():
    """Backup, Totmannschalter, Laptop offline: grau, aber kein Grund für gelb."""
    k = kaesten(beispiel())
    assert k["backup"]["stufe"] == "grau" and bewerten(beispiel())["ampel"]["stufe"] == "gruen"


def lotse_orders(nummer: int, zustand: str = "vorschlag") -> list[dict]:
    notiert = {"ref": f"lotse-{nummer}-1", "ereignis": "notiert", "zeit": "2026-10-08T04:30:05", "lauf": nummer,
               "papier": "SSAC", "seite": "KAUF", "betrag": 294.12, "stueck": 2.8125, "limit": 106.1}
    return [notiert, {"ref": f"lotse-{nummer}-1", "ereignis": zustand, "zeit": "2026-10-08T04:30:06"}]


def test_lotse_letzter_vorschlag_kursquelle_und_budget():
    zeilen = lotse_zeilen(1, "2026-10-07T04:30:01") + lotse_zeilen(2, "2026-10-08T04:30:01")
    zeilen.insert(-2, {"lauf": 2, "ereignis": "kurse", "zeit": "2026-10-08T04:30:03",
                       "kurse": {"SSAC": {"wert": 104.5, "datum": "2026-10-07", "waehrung": "CHF", "quelle": "yahoo"}}})
    orders = lotse_orders(1, "verworfen") + lotse_orders(2)
    jetzt = dt.datetime(2026, 10, 8, 12, 0, tzinfo=ZH).timestamp()
    status, _ = formate.lotse_tagebuch(zeilen, [], orders, jetzt, "America/New_York", 25, budget=300)
    assert status["vorschlaege"] == ["Kauf SSAC 2.8125 Stück, ~294 CHF – nur Vorschlag – nicht gesendet"]  # nur Lauf 2
    assert status["kurse"] == ["SSAC 104.5 CHF – Kurs von Yahoo, 07.10."]
    assert status["budget"] == 300


def test_lotse_ohne_orders_zeigt_das_ergebnis():
    jetzt = dt.datetime(2026, 10, 8, 12, 0, tzinfo=ZH).timestamp()
    status, _ = formate.lotse_tagebuch(lotse_zeilen(1, "2026-10-08T04:30:01"), [], [], jetzt, "America/New_York", 25)
    assert status["vorschlaege"] == ["Nichts zu tun."]


def test_lotse_vollstaendig_auf_bots_seite(tmp_path):
    """Echter Registry-Eintrag, Dateien wie Lotse sie schreibt, Budget aus Lotses config.toml."""
    eintrag = _lotse_ordner(tmp_path / "deploy", lotse_zeilen(1, "2026-10-08T04:30:01"))
    (tmp_path / "deploy" / "state-lotse" / "orders.jsonl").write_text(
        "".join(json.dumps(o) + "\n" for o in lotse_orders(1)), encoding="utf-8")
    (tmp_path / "lotse").mkdir()
    (tmp_path / "lotse" / "config.toml").write_text("[grenzen]\nbudget_chf = 300\n", encoding="utf-8")
    jetzt = dt.datetime(2026, 10, 8, 12, 0, tzinfo=ZH).timestamp()
    roh = beispiel(jetzt)
    roh["container"]["lotse"] = dict(roh["container"]["bot"])
    roh["bots"][1] = messen.bot(str(tmp_path / "deploy"), eintrag, jetzt)
    lotse = kaesten(roh)["bot-lotse"]
    werte = {z["t"]: z.get("w") for z in lotse["zeilen"]}
    assert lotse["stufe"] == "gruen" and lotse["modus"] == "VORSCHLAG"
    assert werte["Budget"] == "300 CHF" and werte["Letzter Vorschlag"].startswith("Kauf SSAC 2.8125 Stück")
    assert lotse["strategie_kurz"] == "ETF-Aufteilung 85/15" and lotse["investiert"] == "52 CHF"
    assert lotse["naechster_lauf"] and lotse["letzter_lauf"]


def test_dritter_bot_hat_alles_fuer_uebersicht_und_bots_seite(tmp_path):
    """Ein dritter Bot nur per Registry: er bekommt dieselben Felder, die Übersicht und Bots-Seite anzeigen."""
    (tmp_path / "bots.toml").write_text('''
[[bot]]
id = "drei"
name = "Bot Drei"
strategie = "Test"
strategie_kurz = "drei Wörter hier"
eingerichtet = true
status_datei = "s.json"
laeufe_datei = "l.jsonl"
waehrung = "USD"
''', encoding="utf-8")
    (tmp_path / "s.json").write_text(json.dumps({"mode": "PAPER", "c_account": {"invested": 10, "positions": {}}}))
    (tmp_path / "l.jsonl").write_text(json.dumps({"ts": 1.76e9, "ok": True, "errors": []}) + "\n")
    roh = beispiel()
    roh["bots"].append(messen.bot(str(tmp_path), messen.registry(str(tmp_path / "bots.toml"))[0], roh["jetzt"]))
    drei = kaesten(roh)["bot-drei"]
    for feld_name in ("titel", "stufe", "modus", "strategie_kurz", "investiert", "letzter_lauf", "zeilen"):
        assert drei.get(feld_name) is not None, feld_name
    js = "".join((ROOT / "dashboard" / "static" / f).read_text(encoding="utf-8") for f in ("uebersicht.js", "bots.js"))
    assert 'startsWith("bot-")' in js and '"bot-c"' not in js and '"bot-lotse"' not in js  # keine fest verdrahteten Bots
