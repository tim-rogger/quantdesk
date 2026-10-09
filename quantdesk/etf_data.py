"""ETF-Universum für Forschungsphase 2: lange Historie, frei von Survivorship-Bias.

Jede Anlageklasse wird durch einen ETF vertreten. Wo der ETF noch nicht existierte, wird er mit einer
dokumentierten Ersatzreihe (Indexfonds, Index oder feste Mischung) verlängert. Verkettung: Bis zum Tag vor
dem ersten ETF-Kurs zählen die Renditen der Ersatzreihe, ab dann der ETF; das Niveau der Ersatzreihe wird so
skaliert, dass es am ersten gemeinsamen Tag ohne Sprung in den ETF übergeht. Jede Reihe kennt ihre Segmente
(`ChainedSeries.segments`, `source_on(tag)`), und jeder Bericht zeigt sie an – nie stillschweigend gemischt.

Quellen (alle gratis): Yahoo Finance (Tageskurse, `adjclose` = inkl. Ausschüttungen), FRED (St. Louis Fed:
USD/CHF, CHF-Zinsen). Die Wahl der Ersatzreihen ist mit Überlappungs-Kennzahlen begründet (DATEN.md).
"""
from __future__ import annotations

import bisect
import csv
import io
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Callable

import requests

from quantdesk.history import CACHE_DIR, Bar, load_history

HISTORY_YEARS = 60  # so weit zurück wie möglich (Yahoo liefert Tagesdaten meist ab 1980)
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"


# --------------------------------------------------------------------------- Beschreibung der Reihen
@dataclass(frozen=True)
class Source:
    symbol: str  # Yahoo-Symbol bzw. "A+B" bei Mischungen
    description: str
    kind: str  # "ETF" | "Indexfonds" | "Index" | "Futures" | "Fonds" | "Mischung"
    total_return: bool = True  # False = nur Kurs, ohne Ausschüttungen
    weights: tuple[tuple[str, float], ...] = ()  # nur bei Mischungen

    @property
    def label(self) -> str:
        return self.symbol + ("" if self.total_return else " (nur Kurs)")


@dataclass(frozen=True)
class AssetSpec:
    key: str  # = ETF-Ticker
    name: str  # Anlageklasse
    etf: Source
    proxy: Source | None
    note: str  # Begründung der Ersatzreihe (Überlappung mit dem ETF)


def _etf(symbol: str, description: str) -> Source:
    return Source(symbol, description, "ETF")


ASSETS: tuple[AssetSpec, ...] = (
    AssetSpec("SPY", "US-Aktien", _etf("SPY", "SPDR S&P 500"),
              Source("VFINX", "Vanguard 500 Index Fund", "Indexfonds"),
              "Überlappung ab 1993: Korrelation (Monat) 0.998, Rendite-Differenz +0.01 % p.a."),
    AssetSpec("EFA", "Aktien Industrieländer ex US", _etf("EFA", "iShares MSCI EAFE"),
              Source("VEURX+VPACX", "60 % Vanguard European + 40 % Vanguard Pacific Stock Index, täglich "
                     "rebalanciert", "Mischung", weights=(("VEURX", 0.6), ("VPACX", 0.4))),
              "Gewichte ≈ EAFE-Regionen in den 1990ern (Europa 55–65 %, Pazifik 35–45 %). Überlappung ab 2001: "
              "Korrelation 0.994, +0.54 % p.a. Japan war Anfang der 1990er schwerer gewichtet -> Ersatz eher zu gut."),
    AssetSpec("EEM", "Aktien Schwellenländer", _etf("EEM", "iShares MSCI Emerging Markets"),
              Source("VEIEX", "Vanguard Emerging Markets Stock Index Fund", "Indexfonds"),
              "Überlappung ab 2003: Korrelation 0.978, −0.18 % p.a."),
    AssetSpec("IEF", "US-Staatsanleihen 7–10 J.", _etf("IEF", "iShares 7-10 Year Treasury"),
              Source("VFITX", "Vanguard Intermediate-Term Treasury Fund", "Fonds"),
              "Überlappung ab 2002: Korrelation 0.982, −0.28 % p.a. (etwas kürzere Duration)."),
    AssetSpec("TLT", "US-Staatsanleihen lang", _etf("TLT", "iShares 20+ Year Treasury"),
              Source("VUSTX", "Vanguard Long-Term Treasury Fund", "Fonds"),
              "Überlappung ab 2002: Korrelation 0.992, +0.07 % p.a."),
    AssetSpec("LQD", "US-Unternehmensanleihen", _etf("LQD", "iShares iBoxx $ Investment Grade Corporate"),
              Source("VFICX", "Vanguard Intermediate-Term Investment-Grade Fund", "Fonds"),
              "Überlappung ab 2002: Korrelation 0.917, −0.19 % p.a. (besser als VWESX: 0.916, +0.33 %)."),
    AssetSpec("GLD", "Gold", _etf("GLD", "SPDR Gold Shares"),
              Source("GC=F", "Gold-Futures COMEX, vorderster Kontrakt", "Futures", total_return=False),
              "Überlappung ab 2004: Korrelation 0.992, +0.49 % p.a. Vor 08/2000 keine freie Tagesreihe für Gold."),
    AssetSpec("DBC", "Rohstoffe", _etf("DBC", "Invesco DB Commodity Index Tracking"),
              Source("PCRIX", "PIMCO CommodityRealReturn Strategy Fund", "Fonds"),
              "Überlappung ab 2006: Korrelation 0.906, −0.50 % p.a. Der S&P-GSCI-Index (^SPGSCI) ist ein "
              "Spot-Index ohne Rollkosten (+3.6 % p.a. über GSG) und wird deshalb NICHT verwendet."),
    AssetSpec("VNQ", "US-Immobilien (REITs)", _etf("VNQ", "Vanguard Real Estate"),
              Source("VGSIX", "Vanguard REIT Index Fund", "Indexfonds"),
              "Überlappung ab 2004: Korrelation 0.999, −0.10 % p.a."),
)

# zusätzlich für Kandidat E/F (gehören nicht zum Trend-Universum von D)
EXTRA_ASSETS: tuple[AssetSpec, ...] = (
    AssetSpec("QQQ", "Nasdaq-100", _etf("QQQ", "Invesco QQQ"),
              Source("^NDX", "Nasdaq-100 Index", "Index", total_return=False),
              "Überlappung ab 1999: Korrelation 0.999, −0.59 % p.a. (Index ohne Dividenden)."),
    AssetSpec("USMV", "US-Aktien minimale Volatilität", _etf("USMV", "iShares MSCI USA Min Vol Factor"), None,
              "kein Ersatz – erst ab 2011"),
    AssetSpec("SPLV", "US-Aktien niedrige Volatilität", _etf("SPLV", "Invesco S&P 500 Low Volatility"), None,
              "kein Ersatz – erst ab 2011"),
    AssetSpec("QUAL", "US-Aktien Qualität", _etf("QUAL", "iShares MSCI USA Quality Factor"), None,
              "kein Ersatz – erst ab 2013"),
)

CORE_KEYS = tuple(a.key for a in ASSETS)
RATE_SYMBOL = "^IRX"  # 13-Wochen-T-Bill in % (Yahoo, ab 1966) = Zins fürs Cash
FX_SERIES = "DEXSZUS"  # FRED: CHF pro USD, täglich ab 1971 (Yahoo CHF=X hat Ausreisser bis 12 %)
CHF_RATE_SERIES = (("IRSTCI01CHM156N", "1972-01-01"), ("IR3TIB01CHM156N", "1999-07-01"))  # FRED, monatlich
CHF_RATE_NOTE = "CHF-Zins: Tagesgeld (FRED IRSTCI01CHM156N) bis 06/1999, ab 07/1999 3-Monats-Interbank (IR3TIB01CHM156N)"


# --------------------------------------------------------------------------- verkettete Reihen
@dataclass(frozen=True)
class Segment:
    source: str
    start: str
    end: str


@dataclass
class ChainedSeries:
    key: str
    name: str
    bars: list[Bar]
    segments: list[Segment]
    warnings: list[str] = field(default_factory=list)

    @property
    def start(self) -> str:
        return self.bars[0].day

    def source_on(self, day: str) -> str | None:
        for seg in self.segments:
            if seg.start <= day <= seg.end:
                return seg.source
        return None


def blend_bars(components: dict[str, list[Bar]], weights: dict[str, float]) -> list[Bar]:
    """Feste Mischung, täglich auf die Gewichte zurückgesetzt, nur an Tagen mit Kursen aller Teile."""
    if abs(sum(weights.values()) - 1) > 1e-9:
        raise ValueError("Gewichte müssen 1 ergeben.")
    closes = {s: {b.day: b.close for b in components[s]} for s in weights}
    days = sorted(set.intersection(*(set(c) for c in closes.values())))
    if not days:
        raise ValueError("Mischung: keine gemeinsamen Tage.")
    value, out = 100.0, [Bar(days[0], 100.0, 100.0, 100.0, 100.0)]
    for prev, day in zip(days, days[1:]):
        value *= 1 + sum(w * (closes[s][day] / closes[s][prev] - 1) for s, w in weights.items())
        out.append(Bar(day, value, value, value, value))
    return out


def chain(etf_bars: list[Bar], proxy_bars: list[Bar] | None, etf_label: str, proxy_label: str | None) -> tuple[list[Bar], list[Segment]]:
    """Ersatzreihe vor den ETF hängen, skaliert auf den ersten gemeinsamen Tag (kein Sprung)."""
    if not etf_bars:
        raise ValueError(f"{etf_label}: keine Kurse")
    etf_start = etf_bars[0].day
    etf_seg = Segment(etf_label, etf_start, etf_bars[-1].day)
    if not proxy_bars or proxy_bars[0].day >= etf_start:
        return list(etf_bars), [etf_seg]
    proxy_by_day = {b.day: b for b in proxy_bars}
    join = next((b for b in etf_bars if b.day in proxy_by_day), None)
    if join is None:
        raise ValueError(f"{etf_label}: Ersatzreihe {proxy_label} hat keinen gemeinsamen Tag mit dem ETF")
    f = join.close / proxy_by_day[join.day].close
    before = [Bar(b.day, b.open * f, b.high * f, b.low * f, b.close * f) for b in proxy_bars if b.day < etf_start]
    if not before:
        return list(etf_bars), [etf_seg]
    return before + list(etf_bars), [Segment(proxy_label, before[0].day, before[-1].day), etf_seg]


def quality_warnings(bars: list[Bar], max_move: float = 0.25, max_gap_days: int = 10) -> list[str]:
    """Verdächtige Stellen melden (nicht verändern): Tagesbewegung > max_move, Lücken > max_gap_days."""
    import datetime as dt

    out = []
    for a, b in zip(bars, bars[1:]):
        r = b.close / a.close - 1
        if abs(r) > max_move:
            out.append(f"{b.day}: Tagesbewegung {r:+.1%}")
        gap = (dt.date.fromisoformat(b.day) - dt.date.fromisoformat(a.day)).days
        if gap > max_gap_days:
            out.append(f"{a.day} → {b.day}: Lücke von {gap} Tagen")
    return out


# --------------------------------------------------------------------------- Laden
def load_fred(series: str, session: requests.Session | None = None, cache_dir: str | None = CACHE_DIR,
              now: float | None = None) -> list[tuple[str, float]]:
    """FRED-Reihe als [(Tag, Wert)], fehlende Werte ('.') ausgelassen. Höchstens einmal pro Tag geladen."""
    now = time.time() if now is None else now
    cache_file = None
    if cache_dir:
        cache_file = os.path.join(cache_dir, f"FRED_{series}_{time.strftime('%Y%m%d', time.gmtime(now))}.json")
        if os.path.exists(cache_file):
            with open(cache_file, encoding="utf-8") as f:
                return [tuple(r) for r in json.load(f)]
    resp = (session or requests).get(FRED_URL.format(series=series), timeout=30)
    resp.raise_for_status()
    rows = []
    for r in csv.DictReader(io.StringIO(resp.text)):
        value = r.get(series, "").strip()
        day = r.get("observation_date") or r.get("DATE")
        if day and value not in ("", "."):
            v = float(value)
            if math.isfinite(v):
                rows.append((day, v))
    if not rows:
        raise ValueError(f"FRED {series}: keine Daten")
    if cache_file:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(rows, f)
    return rows


def chained_rates(parts: list[tuple[list[tuple[str, float]], str]]) -> list[tuple[str, float]]:
    """Mehrere Zinsreihen nacheinander: jede gilt ab ihrem Starttag (bis der nächste Teil beginnt)."""
    out: list[tuple[str, float]] = []
    starts = [s for _, s in parts] + ["9999-12-31"]
    for (rows, start), nxt in zip(parts, starts[1:]):
        out += [(d, v) for d, v in rows if start <= d < nxt]
    return out


Loader = Callable[[str], list[Bar]]


def _default_loader(symbol: str) -> list[Bar]:
    return load_history(symbol, years=HISTORY_YEARS)


def build_asset(spec: AssetSpec, loader: Loader = _default_loader) -> ChainedSeries:
    etf_bars = loader(spec.etf.symbol)
    proxy_bars = None
    if spec.proxy is not None:
        if spec.proxy.weights:
            parts = {s: loader(s) for s, _ in spec.proxy.weights}
            proxy_bars = blend_bars(parts, dict(spec.proxy.weights))
        else:
            proxy_bars = loader(spec.proxy.symbol)
    bars, segments = chain(etf_bars, proxy_bars, spec.etf.label, spec.proxy.label if spec.proxy else None)
    series = ChainedSeries(spec.key, spec.name, bars, segments)
    series.warnings = quality_warnings(bars)
    return series


@dataclass
class EtfUniverse:
    series: dict[str, ChainedSeries]
    rates: list[tuple[str, float]]  # T-Bill-Jahreszins als Dezimalzahl
    fx: list[tuple[str, float]]  # CHF pro USD
    chf_rates: list[tuple[str, float]]  # CHF-Kurzfristzins als Dezimalzahl
    missing: dict[str, str] = field(default_factory=dict)  # key -> Fehler

    def bars(self) -> dict[str, list[Bar]]:
        return {k: s.bars for k, s in self.series.items()}


def load_universe(specs: tuple[AssetSpec, ...] = ASSETS + EXTRA_ASSETS, loader: Loader = _default_loader,
                  fred: Callable[[str], list[tuple[str, float]]] = load_fred) -> EtfUniverse:
    series, missing = {}, {}
    for spec in specs:
        try:
            series[spec.key] = build_asset(spec, loader)
        except (requests.RequestException, ValueError) as e:
            missing[spec.key] = str(e)
    rates = [(b.day, b.close / 100) for b in loader(RATE_SYMBOL)]
    fx = fred(FX_SERIES)
    chf = chained_rates([([(d, v / 100) for d, v in fred(sid)], start) for sid, start in CHF_RATE_SERIES])
    return EtfUniverse(series, rates, fx, chf, missing)


def available(universe: EtfUniverse, day: str, keys: tuple[str, ...] = CORE_KEYS, warmup: int = 0) -> list[str]:
    """Anlageklassen, die an `day` schon mindestens `warmup` Handelstage Historie haben."""
    out = []
    for k in keys:
        s = universe.series.get(k)
        if s is None:
            continue
        days = [b.day for b in s.bars]
        i = bisect.bisect_right(days, day)
        if i > warmup:
            out.append(k)
    return out


def coverage_rows(universe: EtfUniverse, specs: tuple[AssetSpec, ...] = ASSETS + EXTRA_ASSETS) -> list[dict]:
    rows = []
    for spec in specs:
        s = universe.series.get(spec.key)
        if s is None:
            rows.append({"key": spec.key, "name": spec.name, "error": universe.missing.get(spec.key, "fehlt")})
            continue
        rows.append({
            "key": spec.key, "name": spec.name, "start": s.start, "end": s.bars[-1].day,
            "etf_start": s.segments[-1].start,
            "chain": " → ".join(f"{seg.source} ({seg.start[:7]})" for seg in s.segments),
            "note": spec.note, "warnings": s.warnings,
        })
    return rows
