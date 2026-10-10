"""Eingefrorene Datenstände (Snapshots) für reproduzierbare Forschung.

Yahoo rechnet `adjclose` rückwirkend neu (nachgetragene Dividenden), FRED revidiert Reihen. Damit ein Ergebnis
sich nicht still verändert, arbeitet jeder Forschungslauf mit einem benannten Datenstand:

    research/data/snapshot-2026-10-09/
        MANIFEST.json            Quelle, URL, Abrufzeit, Zeilen, erster/letzter Tag, SHA-256 je Reihe
        yahoo_SPY.raw.gz         Rohantwort der Quelle, unverändert (nur komprimiert)
        fred_DEXSZUS.raw.gz      ...

Bestehende Snapshots werden nie überschrieben (neuer Name, atomar angelegt). Beim Lesen wird jede Prüfsumme
kontrolliert. Die Rohdateien stehen nicht im Git (Nutzungsbedingungen der Quellen); MANIFEST.json schon –
damit lässt sich überall prüfen, ob ein lokaler Datenstand derselbe ist.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from quantdesk.history import Bar, fetch_yahoo_raw, parse_yahoo_history, yahoo_url

SNAPSHOT_ROOT = os.path.join("research", "data")
PREFIX = "snapshot-"
MANIFEST = "MANIFEST.json"
YAHOO_YEARS = 60
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"


class SnapshotError(Exception):
    pass


@dataclass(frozen=True)
class SeriesId:
    provider: str  # "yahoo" | "fred"
    symbol: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.symbol}"

    @property
    def filename(self) -> str:
        return f"{self.provider}_{re.sub(r'[^A-Za-z0-9._-]', '_', self.symbol)}.raw.gz"

    @classmethod
    def from_key(cls, key: str) -> "SeriesId":
        provider, symbol = key.split(":", 1)
        return cls(provider, symbol)


# --------------------------------------------------------------------------- Rohdaten holen und lesen
def fetch_fred_raw(series: str, session=None) -> bytes:
    import requests

    resp = (session or requests).get(FRED_URL.format(series=series), timeout=30)
    resp.raise_for_status()
    return resp.content if hasattr(resp, "content") else resp.text.encode()


def parse_fred(text: str, series: str) -> list[tuple[str, float]]:
    import csv
    import io
    import math

    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        value = (r.get(series) or "").strip()
        day = r.get("observation_date") or r.get("DATE")
        if day and value not in ("", "."):
            v = float(value)
            if math.isfinite(v):
                rows.append((day, v))
    if not rows:
        raise ValueError(f"FRED {series}: keine Daten")
    return rows


def fetch_raw(sid: SeriesId, session=None, now: float | None = None) -> bytes:
    if sid.provider == "yahoo":
        return fetch_yahoo_raw(sid.symbol, YAHOO_YEARS, session, now)
    if sid.provider == "fred":
        return fetch_fred_raw(sid.symbol, session)
    raise ValueError(f"unbekannte Quelle {sid.provider}")


def source_url(sid: SeriesId, now: float) -> str:
    return yahoo_url(sid.symbol, YAHOO_YEARS, now) if sid.provider == "yahoo" else FRED_URL.format(series=sid.symbol)


def parse(sid: SeriesId, raw: bytes) -> list[Bar] | list[tuple[str, float]]:
    if sid.provider == "yahoo":
        bars = parse_yahoo_history(json.loads(raw))
        if not bars:
            raise ValueError(f"Keine Kursdaten für {sid.symbol}")
        return bars
    return parse_fred(raw.decode("utf-8"), sid.symbol)


def _days(rows) -> list[str]:
    return [r.day if isinstance(r, Bar) else r[0] for r in rows]


# --------------------------------------------------------------------------- anlegen
def _new_name(root: str, day: str) -> str:
    base = f"{PREFIX}{day}"
    name, n = base, 1
    while os.path.exists(os.path.join(root, name)) or os.path.exists(os.path.join(root, name + ".tmp")):
        n += 1
        name = f"{base}-{n}"
    return name


def create(series: Iterable[SeriesId], root: str = SNAPSHOT_ROOT,
           fetch: Callable[[SeriesId], bytes] = fetch_raw, clock: Callable[[], float] = time.time,
           progress: Callable[[int, int, SeriesId], None] | None = None) -> str:
    """Neuen Datenstand anlegen und dessen Pfad liefern. Scheitert eine Reihe, wird nichts angelegt."""
    series = list(dict.fromkeys(series))
    now = clock()
    day = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%d")
    os.makedirs(root, exist_ok=True)
    name = _new_name(root, day)
    final, tmp = os.path.join(root, name), os.path.join(root, name + ".tmp")
    os.makedirs(tmp)
    entries = {}
    try:
        for i, sid in enumerate(series, 1):
            if progress:
                progress(i, len(series), sid)
            raw = fetch(sid)
            rows = parse(sid, raw)  # nur Rohdaten speichern, die sich auch lesen lassen
            days = _days(rows)
            with gzip.open(os.path.join(tmp, sid.filename), "wb") as f:
                f.write(raw)
            entries[sid.key] = {
                "provider": sid.provider, "symbol": sid.symbol, "url": source_url(sid, now),
                "fetched_utc": dt.datetime.fromtimestamp(clock(), dt.timezone.utc).isoformat(timespec="seconds"),
                "rows": len(rows), "first_day": days[0], "last_day": days[-1],
                "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "file": sid.filename,
            }
        manifest = {"name": name, "created_utc": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="seconds"),
                    "series": entries}
        with open(os.path.join(tmp, MANIFEST), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        os.rename(tmp, final)  # erst jetzt sichtbar; ein bestehender Name wird nie wiederverwendet
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final


# --------------------------------------------------------------------------- lesen
class Snapshot:
    def __init__(self, path: str):
        self.path = path
        try:
            with open(os.path.join(path, MANIFEST), encoding="utf-8") as f:
                self.manifest = json.load(f)
        except FileNotFoundError as e:
            raise SnapshotError(f"{path}: kein {MANIFEST} – kein gültiger Datenstand") from e
        self.name = self.manifest["name"]
        self._cache: dict[str, object] = {}

    @property
    def created(self) -> str:
        return self.manifest["created_utc"]

    @property
    def keys(self) -> list[str]:
        return list(self.manifest["series"])

    def raw(self, sid: SeriesId) -> bytes:
        entry = self.manifest["series"].get(sid.key)
        if entry is None:
            raise SnapshotError(f"{self.name}: Reihe {sid.key} fehlt im Datenstand")
        file = os.path.join(self.path, entry["file"])
        if not os.path.exists(file):
            raise SnapshotError(f"{self.name}: Rohdatei {entry['file']} fehlt (nur MANIFEST vorhanden?)")
        with gzip.open(file, "rb") as f:
            raw = f.read()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise SnapshotError(f"{self.name}: Prüfsumme von {sid.key} stimmt nicht – Datei verändert")
        return raw

    def rows(self, sid: SeriesId):
        if sid.key not in self._cache:
            self._cache[sid.key] = parse(sid, self.raw(sid))
        return self._cache[sid.key]

    def yahoo(self, symbol: str) -> list[Bar]:
        return self.rows(SeriesId("yahoo", symbol))

    def fred(self, series: str) -> list[tuple[str, float]]:
        return self.rows(SeriesId("fred", series))

    def verify(self) -> list[str]:
        problems = []
        for key in self.keys:
            try:
                self.raw(SeriesId.from_key(key))
            except SnapshotError as e:
                problems.append(str(e))
        return problems

    def header(self) -> str:
        return f"Datenstand {self.name} (abgerufen {self.created[:16].replace('T', ' ')} UTC, {len(self.keys)} Reihen)"


def list_snapshots(root: str = SNAPSHOT_ROOT) -> list[str]:
    if not os.path.isdir(root):
        return []
    return sorted((n for n in os.listdir(root)
                   if n.startswith(PREFIX) and not n.endswith(".tmp") and os.path.isfile(os.path.join(root, n, MANIFEST))),
                  key=lambda n: (n[:len(PREFIX) + 10], int(n[len(PREFIX) + 11:] or 1)))


def open_snapshot(name: str | None = None, root: str = SNAPSHOT_ROOT) -> Snapshot:
    """Benannten Datenstand öffnen; ohne Namen den neusten."""
    if name is None:
        names = list_snapshots(root)
        if not names:
            raise SnapshotError("Kein Datenstand vorhanden – zuerst `python research_etf.py snapshot --neu`.")
        name = names[-1]
    path = name if os.path.isdir(name) else os.path.join(root, name)
    if not os.path.isdir(path):
        raise SnapshotError(f"Datenstand {name} nicht gefunden (vorhanden: {', '.join(list_snapshots(root)) or 'keiner'})")
    return Snapshot(path)


# --------------------------------------------------------------------------- vergleichen
@dataclass(frozen=True)
class SeriesDiff:
    key: str
    status: str  # gleich | geändert | neu | entfernt
    common_days: int = 0
    changed_days: int = 0  # Tage im gemeinsamen Zeitraum mit anderer Tagesrendite (Yahoo) bzw. anderem Wert (FRED)
    max_change: float = 0.0  # grösste Abweichung (Rendite- bzw. relative Wert-Differenz)
    cagr_change: float = 0.0  # Yahoo: Rendite p.a. über den gemeinsamen Zeitraum, neu − alt
    added_days: int = 0  # neue Tage nach dem alten Ende (normale Fortschreibung)
    missing_days: int = 0  # Tage, die im neuen Stand fehlen

    @property
    def retroactive(self) -> bool:
        return self.changed_days > 0 or self.missing_days > 0


def _yahoo_diff(key: str, old: list[Bar], new: list[Bar], tol: float) -> SeriesDiff:
    o = {b.day: b.close for b in old}
    n = {b.day: b.close for b in new}
    common = sorted(set(o) & set(n))
    changed, worst = 0, 0.0
    for a, b in zip(common, common[1:]):
        d = abs((n[b] / n[a]) - (o[b] / o[a]))
        if d > tol:
            changed += 1
            worst = max(worst, d)
    cagr = 0.0
    if len(common) > 1:
        years = (dt.date.fromisoformat(common[-1]) - dt.date.fromisoformat(common[0])).days / 365.25
        if years > 0:
            cagr = (n[common[-1]] / n[common[0]]) ** (1 / years) - (o[common[-1]] / o[common[0]]) ** (1 / years)
    last_old = old[-1].day
    return SeriesDiff(key, "geändert", len(common), changed, worst, cagr,
                      added_days=sum(1 for d in n if d > last_old),
                      missing_days=sum(1 for d in o if d not in n))


def _fred_diff(key: str, old, new, tol: float) -> SeriesDiff:
    o, n = dict(old), dict(new)
    common = sorted(set(o) & set(n))
    rel = [abs(n[d] - o[d]) / max(abs(o[d]), 1e-12) for d in common]
    changed = [r for r in rel if r > tol]
    last_old = old[-1][0]
    return SeriesDiff(key, "geändert", len(common), len(changed), max(changed, default=0.0), 0.0,
                      added_days=sum(1 for d in n if d > last_old),
                      missing_days=sum(1 for d in o if d not in n))


def compare(old: Snapshot, new: Snapshot, tol: float = 1e-6) -> list[SeriesDiff]:
    out = []
    for key in sorted(set(old.keys) | set(new.keys)):
        if key not in new.keys:
            out.append(SeriesDiff(key, "entfernt"))
            continue
        if key not in old.keys:
            out.append(SeriesDiff(key, "neu"))
            continue
        if old.manifest["series"][key]["sha256"] == new.manifest["series"][key]["sha256"]:
            out.append(SeriesDiff(key, "gleich"))
            continue
        sid = SeriesId.from_key(key)
        a, b = old.rows(sid), new.rows(sid)
        d = (_yahoo_diff if sid.provider == "yahoo" else _fred_diff)(key, a, b, tol)
        if not d.retroactive and d.added_days == 0:
            d = SeriesDiff(key, "gleich", d.common_days)  # nur anders formatiert/skaliert, Renditen identisch
        out.append(d)
    return out
