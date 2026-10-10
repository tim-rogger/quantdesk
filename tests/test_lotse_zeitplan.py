"""Lotse – Zeitplan: täglich an SIX-Handelstagen 10:30 Zürich, keine Läufe an Feiertagen, Nachholen nach Neustart."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from lotse import zeitplan
from lotse.lauf import lies_config
from lotse.tagebuch import Tagebuch

ZUERICH = ZoneInfo("Europe/Zurich")
UM_1030 = dt.time(10, 30)
BIS_1700 = dt.time(17, 0)


def zh(jahr, monat, tag, stunde=0, minute=0):
    return dt.datetime(jahr, monat, tag, stunde, minute, tzinfo=ZUERICH)


def test_config_zeitplan_und_limit():
    cfg, _ = lies_config()
    assert cfg["zeitplan"] == {"uhrzeit": "10:30", "zeitzone": "Europe/Zurich", "nachholen_bis": "17:00",
                               "lauf_timeout_minuten": 20}
    assert cfg["ausfuehren"]["limit_abstand_prozent"] == 1.5


@pytest.mark.parametrize("jetzt,erwartet", [
    (zh(2026, 10, 9, 9, 0), zh(2026, 10, 9, 10, 30)),     # Freitag vor 10:30: heute
    (zh(2026, 10, 9, 10, 30), zh(2026, 10, 12, 10, 30)),  # genau 10:30 ist vorbei: nächster Handelstag (Montag)
    (zh(2026, 10, 10, 12, 0), zh(2026, 10, 12, 10, 30)),  # Samstag
    (zh(2026, 12, 23, 11, 0), zh(2026, 12, 28, 10, 30)),  # Heiligabend + Weihnachten + Stephanstag zu
    (zh(2026, 12, 30, 11, 0), zh(2027, 1, 4, 10, 30)),    # Silvester, Neujahr, Berchtoldstag zu
    (zh(2026, 5, 13, 11, 0), zh(2026, 5, 15, 10, 30)),    # Auffahrt
])
def test_naechster_lauf_nur_an_six_handelstagen(jetzt, erwartet):
    assert zeitplan.naechster_lauf(jetzt, UM_1030, ZUERICH) == erwartet


def test_sommerzeit_ende_bleibt_1030_zuerich():
    termin = zeitplan.naechster_lauf(zh(2026, 10, 23, 11, 0), UM_1030, ZUERICH)  # Fr; So 25.10. Umstellung
    assert termin == zh(2026, 10, 26, 10, 30)
    assert termin.astimezone(dt.timezone.utc).hour == 9  # Winterzeit: 10:30 MEZ = 09:30 UTC
    vorher = zeitplan.naechster_lauf(zh(2026, 10, 22, 11, 0), UM_1030, ZUERICH)
    assert vorher.astimezone(dt.timezone.utc).hour == 8  # Sommerzeit: 10:30 MESZ = 08:30 UTC


@pytest.mark.parametrize("jetzt,gelaufen,erwartet", [
    (zh(2026, 10, 9, 11, 0), False, True),    # Neustart nach 10:30, heute noch kein Lauf
    (zh(2026, 10, 9, 11, 0), True, False),    # schon gelaufen
    (zh(2026, 10, 9, 9, 0), False, False),    # vor 10:30: normal warten
    (zh(2026, 10, 9, 17, 30), False, False),  # nach 17:00: nicht mehr
    (zh(2026, 12, 24, 11, 0), False, False),  # Feiertag
])
def test_nachholen_nach_neustart(jetzt, gelaufen, erwartet):
    assert zeitplan.jetzt_nachholen(jetzt, UM_1030, BIS_1700, ZUERICH, gelaufen) is erwartet


class Uhr:
    def __init__(self, start):
        self.zeit = start

    def __call__(self):
        return self.zeit

    def schlafen(self, sekunden):
        self.zeit += dt.timedelta(seconds=sekunden)


def test_schleife_startet_je_handelstag_einen_lauf(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTSE_ORDNER", str(tmp_path))
    uhr = Uhr(zh(2026, 12, 23, 9, 0))
    gestartet = []

    def starte(timeout):
        gestartet.append(uhr())
        return 0

    assert zeitplan.main(uhr=uhr, schlafen=uhr.schlafen, starte=starte, runden=2) == 0
    assert gestartet == [zh(2026, 12, 23, 10, 30), zh(2026, 12, 28, 10, 30)]  # 24.–27.12. kein Lauf


def test_nachholen_beim_start_und_kein_doppellauf(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTSE_ORDNER", str(tmp_path))
    uhr = Uhr(zh(2026, 10, 9, 11, 0))
    gestartet = []
    zeitplan.main(uhr=uhr, schlafen=uhr.schlafen, starte=lambda t: gestartet.append(uhr()) or 0, runden=1)
    assert gestartet == [zh(2026, 10, 9, 11, 0), zh(2026, 10, 12, 10, 30)]  # sofort nachgeholt, dann Montag

    Tagebuch(str(tmp_path)).neuer_lauf("x", dt.datetime(2026, 10, 9, 10, 30), "VORSCHLAG")
    gestartet.clear()
    uhr = Uhr(zh(2026, 10, 9, 11, 0))
    zeitplan.main(uhr=uhr, schlafen=uhr.schlafen, starte=lambda t: gestartet.append(uhr()) or 0, runden=1)
    assert gestartet == [zh(2026, 10, 12, 10, 30)]  # heute schon gelaufen: nicht nochmals


def test_absturz_eines_laufs_gibt_push_und_zeitplan_laeuft_weiter(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTSE_ORDNER", str(tmp_path))
    gesendet = []
    monkeypatch.setattr(zeitplan.Push, "sende", lambda self, titel, text, wichtig=False: gesendet.append(titel))
    uhr = Uhr(zh(2026, 10, 9, 9, 0))
    codes = iter([2, 0])
    zeitplan.main(uhr=uhr, schlafen=uhr.schlafen, starte=lambda t: next(codes), runden=2)
    assert gesendet == ["Lotse – Lauf abgestürzt"]  # Exit 2 = Absturz; Exit 1 (Regel 0) pusht der Lauf selbst
