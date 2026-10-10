"""Eingefrorene Datenstände: anlegen ohne Überschreiben, Prüfsummen, Vergleich rückwirkender Änderungen."""
import gzip
import json
import os

import pytest

import research_etf
from quantdesk import snapshot as sn
from quantdesk.snapshot import SeriesId, SnapshotError


def yahoo_raw(closes, start=1_000_000_000, adj_factor=1.0):
    """Rohantwort im Format der Yahoo-Chart-API."""
    ts = [start + i * 86400 for i in range(len(closes))]
    return json.dumps({"chart": {"result": [{
        "timestamp": ts,
        "indicators": {"quote": [{"open": closes, "high": closes, "low": closes, "close": closes}],
                       "adjclose": [{"adjclose": [c * adj_factor for c in closes]}]}}]}}).encode()


FRED = b"observation_date,DEXSZUS\n2001-09-09,1.5\n2001-09-10,1.51\n"


class Source:
    def __init__(self):
        self.data = {"yahoo:AAA": yahoo_raw([10.0, 11.0, 12.0]), "fred:DEXSZUS": FRED}
        self.calls = 0

    def __call__(self, sid):
        self.calls += 1
        return self.data[sid.key]


SERIES = [SeriesId("yahoo", "AAA"), SeriesId("fred", "DEXSZUS")]


def clock(t=1_791_550_000.0):  # 2026-10-09 12:46 UTC
    return lambda: t


def test_create_writes_raw_and_manifest_and_never_overwrites(tmp_path):
    src = Source()
    p1 = sn.create(SERIES, str(tmp_path), fetch=src, clock=clock())
    p2 = sn.create(SERIES, str(tmp_path), fetch=src, clock=clock())
    assert os.path.basename(p1) == "snapshot-2026-10-09" and os.path.basename(p2) == "snapshot-2026-10-09-2"
    m = json.loads((tmp_path / "snapshot-2026-10-09" / "MANIFEST.json").read_text(encoding="utf-8"))
    e = m["series"]["yahoo:AAA"]
    assert e["rows"] == 3 and e["first_day"] == "2001-09-09" and len(e["sha256"]) == 64 and "period1=" in e["url"]
    assert m["series"]["fred:DEXSZUS"]["rows"] == 2
    with gzip.open(tmp_path / "snapshot-2026-10-09" / e["file"], "rb") as f:
        assert f.read() == src.data["yahoo:AAA"]  # Rohdaten unverändert
    assert sn.list_snapshots(str(tmp_path)) == ["snapshot-2026-10-09", "snapshot-2026-10-09-2"]
    assert sn.open_snapshot(root=str(tmp_path)).name == "snapshot-2026-10-09-2"


def test_failed_fetch_leaves_nothing(tmp_path):
    def broken(sid):
        if sid.provider == "fred":
            raise OSError("FRED down")
        return yahoo_raw([1.0, 2.0])

    with pytest.raises(OSError):
        sn.create(SERIES, str(tmp_path), fetch=broken, clock=clock())
    assert os.listdir(tmp_path) == []


def test_read_verifies_checksum(tmp_path):
    path = sn.create(SERIES, str(tmp_path), fetch=Source(), clock=clock())
    s = sn.Snapshot(path)
    assert [b.close for b in s.yahoo("AAA")] == [10.0, 11.0, 12.0] and s.fred("DEXSZUS")[1] == ("2001-09-10", 1.51)
    assert s.verify() == [] and s.header().startswith("Datenstand snapshot-2026-10-09 (abgerufen 2026-10-09")
    with gzip.open(os.path.join(path, "yahoo_AAA.raw.gz"), "wb") as f:
        f.write(yahoo_raw([10.0, 11.0, 99.0]))
    with pytest.raises(SnapshotError, match="Prüfsumme"):
        sn.Snapshot(path).yahoo("AAA")
    os.remove(os.path.join(path, "fred_DEXSZUS.raw.gz"))
    assert len(sn.Snapshot(path).verify()) == 2
    with pytest.raises(SnapshotError, match="fehlt"):
        sn.Snapshot(path).yahoo("ZZZ")


def test_open_without_snapshot_explains(tmp_path):
    with pytest.raises(SnapshotError, match="snapshot --neu"):
        sn.open_snapshot(root=str(tmp_path))


def test_compare_finds_retroactive_changes_not_rescaling(tmp_path):
    src = Source()
    old = sn.Snapshot(sn.create(SERIES, str(tmp_path), fetch=src, clock=clock()))
    # 1) adjclose nach einer Dividende neu skaliert + ein Tag angehängt -> keine rückwirkende Änderung
    src.data["yahoo:AAA"] = yahoo_raw([10.0, 11.0, 12.0, 12.5], adj_factor=0.97)
    rescaled = sn.Snapshot(sn.create(SERIES, str(tmp_path), fetch=src, clock=clock()))
    by = {d.key: d for d in sn.compare(old, rescaled)}
    assert by["yahoo:AAA"].status == "geändert" and not by["yahoo:AAA"].retroactive and by["yahoo:AAA"].added_days == 1
    assert by["fred:DEXSZUS"].status == "gleich"
    # 2) echte Revision: Tagesrendite in der Vergangenheit anders, FRED-Wert revidiert
    src.data["yahoo:AAA"] = yahoo_raw([10.0, 11.5, 12.0])
    src.data["fred:DEXSZUS"] = b"observation_date,DEXSZUS\n2001-09-09,1.5\n2001-09-10,1.52\n"
    revised = sn.Snapshot(sn.create(SERIES, str(tmp_path), fetch=src, clock=clock()))
    by = {d.key: d for d in sn.compare(old, revised)}
    assert by["yahoo:AAA"].retroactive and by["yahoo:AAA"].changed_days == 2
    assert by["yahoo:AAA"].max_change == pytest.approx(0.05, abs=1e-9)
    assert by["fred:DEXSZUS"].changed_days == 1 and by["fred:DEXSZUS"].max_change == pytest.approx(0.01 / 1.51)


def test_cli_list_check_compare(tmp_path, capsys):
    src = Source()
    sn.create(SERIES, str(tmp_path), fetch=src, clock=clock())
    src.data["yahoo:AAA"] = yahoo_raw([10.0, 11.5, 12.0])
    sn.create(SERIES, str(tmp_path), fetch=src, clock=clock())
    root = ["--root", str(tmp_path)]
    assert research_etf.main(root + ["snapshot", "--liste"]) == 0
    assert research_etf.main(root + ["snapshot", "--pruefe"]) == 0
    assert research_etf.main(root + ["snapshot", "--vergleiche", "snapshot-2026-10-09", "snapshot-2026-10-09-2"]) == 0
    out = capsys.readouterr().out
    assert "snapshot-2026-10-09-2" in out and "Prüfsummen: alle in Ordnung." in out
    assert "Rückwirkend geändert: yahoo:AAA" in out
    assert research_etf.main(["--root", str(tmp_path / "leer"), "daten"]) == 1
