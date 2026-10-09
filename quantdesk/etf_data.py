"""ETF-Universum für Forschungsphase 2: lange Historie, frei von Survivorship-Bias.

Jede Anlageklasse wird durch einen ETF vertreten. Wo der ETF noch nicht existierte, wird er mit einer
dokumentierten Ersatzreihe (Indexfonds, Index oder feste Mischung) verlängert. Verkettung: Bis zum Tag vor
dem ersten ETF-Kurs zählen die Renditen der Ersatzreihe, ab dann der ETF; das Niveau der Ersatzreihe wird so
skaliert, dass es am ersten gemeinsamen Tag ohne Sprung in den ETF übergeht. Jede Reihe kennt ihre Segmente
(`ChainedSeries.segments`, `source_on(tag)`), und jeder Bericht zeigt sie an – nie stillschweigend gemischt.

Drei Fassungen (jeder Kandidat wird in allen dreien ausgewertet, siehe research/DATEN.md):
  A  volle verkettete Historie
  B  nur echte ETF-Daten (keine Ersatzreihen)
  C  volle Historie, Ersatzreihen um ihre in der Überlappung gemessene Abweichung p.a. korrigiert

Daten kommen ausschliesslich aus einem eingefrorenen Datenstand (quantdesk.snapshot).
"""
from __future__ import annotations

import bisect
import datetime as dt
import math
import statistics
from dataclasses import dataclass, field
from typing import Callable

from quantdesk.history import Bar
from quantdesk.snapshot import SeriesId, Snapshot

VARIANTS = {
    "A": "volle verkettete Historie",
    "B": "nur echte ETF-Daten",
    "C": "verkettet, Ersatzreihen um gemessene Abweichung korrigiert",
}


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

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(s for s, _ in self.weights) if self.weights else (self.symbol,)


@dataclass(frozen=True)
class AssetSpec:
    key: str  # = ETF-Ticker
    name: str  # Anlageklasse
    etf: Source
    proxy: Source | None
    note: str  # Begründung der Ersatzreihe (die Kennzahlen dazu rechnet overlap_stats)


def _etf(symbol: str, description: str) -> Source:
    return Source(symbol, description, "ETF")


ASSETS: tuple[AssetSpec, ...] = (
    AssetSpec("SPY", "US-Aktien", _etf("SPY", "SPDR S&P 500"),
              Source("VFINX", "Vanguard 500 Index Fund", "Indexfonds"), "gleicher Index"),
    AssetSpec("EFA", "Aktien Industrieländer ex US", _etf("EFA", "iShares MSCI EAFE"),
              Source("VEURX+VPACX", "60 % Vanguard European + 40 % Vanguard Pacific Stock Index, täglich "
                     "rebalanciert", "Mischung", weights=(("VEURX", 0.6), ("VPACX", 0.4))),
              "Gewichte ≈ EAFE-Regionen in den 1990ern (Europa 55–65 %, Pazifik 35–45 %); Japan war Anfang der "
              "1990er schwerer gewichtet -> Ersatz in der Japan-Baisse eher zu gut"),
    AssetSpec("EEM", "Aktien Schwellenländer", _etf("EEM", "iShares MSCI Emerging Markets"),
              Source("VEIEX", "Vanguard Emerging Markets Stock Index Fund", "Indexfonds"), "Indexfonds"),
    AssetSpec("IEF", "US-Staatsanleihen 7–10 J.", _etf("IEF", "iShares 7-10 Year Treasury"),
              Source("VFITX", "Vanguard Intermediate-Term Treasury Fund", "Fonds"), "etwas kürzere Duration"),
    AssetSpec("TLT", "US-Staatsanleihen lang", _etf("TLT", "iShares 20+ Year Treasury"),
              Source("VUSTX", "Vanguard Long-Term Treasury Fund", "Fonds"), "ähnliche Duration"),
    AssetSpec("LQD", "US-Unternehmensanleihen", _etf("LQD", "iShares iBoxx $ Investment Grade Corporate"),
              Source("VFICX", "Vanguard Intermediate-Term Investment-Grade Fund", "Fonds"),
              "passt besser als VWESX (lange Laufzeit)"),
    AssetSpec("GLD", "Gold", _etf("GLD", "SPDR Gold Shares"),
              Source("GC=F", "Gold-Futures COMEX, vorderster Kontrakt", "Futures", total_return=False),
              "vor 08/2000 keine freie Tagesreihe für Gold (Minenfonds sind kein Gold)"),
    AssetSpec("DBC", "Rohstoffe", _etf("DBC", "Invesco DB Commodity Index Tracking"),
              Source("PCRIX", "PIMCO CommodityRealReturn Strategy Fund", "Fonds"),
              "^SPGSCI ist ein Spot-Index ohne Rollkosten (ab 2006 +3.6 % p.a. über GSG) und wird NICHT verwendet"),
    AssetSpec("VNQ", "US-Immobilien (REITs)", _etf("VNQ", "Vanguard Real Estate"),
              Source("VGSIX", "Vanguard REIT Index Fund", "Indexfonds"), "gleicher Index"),
)

# zusätzlich für Kandidat E/F (gehören nicht zum Trend-Universum von D)
EXTRA_ASSETS: tuple[AssetSpec, ...] = (
    AssetSpec("QQQ", "Nasdaq-100", _etf("QQQ", "Invesco QQQ"),
              Source("^NDX", "Nasdaq-100 Index", "Index", total_return=False), "Index ohne Dividenden"),
    AssetSpec("USMV", "US-Aktien minimale Volatilität", _etf("USMV", "iShares MSCI USA Min Vol Factor"), None,
              "kein Ersatz – erst ab 2011"),
    AssetSpec("SPLV", "US-Aktien niedrige Volatilität", _etf("SPLV", "Invesco S&P 500 Low Volatility"), None,
              "kein Ersatz – erst ab 2011"),
    AssetSpec("QUAL", "US-Aktien Qualität", _etf("QUAL", "iShares MSCI USA Quality Factor"), None,
              "kein Ersatz – erst ab 2013"),
)

ALL_ASSETS = ASSETS + EXTRA_ASSETS
CORE_KEYS = tuple(a.key for a in ASSETS)
RATE_SYMBOL = "^IRX"  # 13-Wochen-T-Bill in % (Yahoo, ab 1966) = Zins fürs Cash
FX_SERIES = "DEXSZUS"  # FRED: CHF pro USD, täglich ab 1971 (Yahoo CHF=X hat Ausreisser bis 12 %)
CHF_RATE_SERIES = (("IRSTCI01CHM156N", "1972-01-01"), ("IR3TIB01CHM156N", "1999-07-01"))  # FRED, monatlich
CHF_RATE_NOTE = "CHF-Zins: Tagesgeld (FRED IRSTCI01CHM156N) bis 06/1999, ab 07/1999 3-Monats-Interbank (IR3TIB01CHM156N)"


def required_series(specs: tuple[AssetSpec, ...] = ALL_ASSETS) -> list[SeriesId]:
    """Alle Rohreihen, die ein Datenstand enthalten muss."""
    out = []
    for spec in specs:
        out.append(SeriesId("yahoo", spec.etf.symbol))
        if spec.proxy is not None:
            out += [SeriesId("yahoo", s) for s in spec.proxy.symbols]
    out.append(SeriesId("yahoo", RATE_SYMBOL))
    out.append(SeriesId("fred", FX_SERIES))
    out += [SeriesId("fred", sid) for sid, _ in CHF_RATE_SERIES]
    return list(dict.fromkeys(out))


# --------------------------------------------------------------------------- verkettete Reihen
@dataclass(frozen=True)
class Segment:
    source: str
    start: str
    end: str


@dataclass(frozen=True)
class Overlap:
    """Ersatzreihe gegen ETF im gemeinsamen Zeitraum (Monatsrenditen)."""
    start: str
    end: str
    months: int
    correlation: float
    etf_cagr: float
    proxy_cagr: float
    tracking_error: float

    @property
    def diff(self) -> float:
        return self.proxy_cagr - self.etf_cagr

    def text(self) -> str:
        return (f"Überlappung {self.start[:7]}–{self.end[:7]}: Korrelation {self.correlation:.3f}, "
                f"Abweichung {self.diff * 100:+.2f} % p.a., Tracking Error {self.tracking_error:.1%}")


@dataclass
class ChainedSeries:
    key: str
    name: str
    bars: list[Bar]
    segments: list[Segment]
    warnings: list[str] = field(default_factory=list)
    overlap: Overlap | None = None
    correction: float = 0.0  # Fassung C: Korrektur der Ersatzreihe in % p.a. (−Abweichung)

    @property
    def start(self) -> str:
        return self.bars[0].day

    @property
    def etf_start(self) -> str:
        return self.segments[-1].start

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


def _month_ends(closes: dict[str, float], days: list[str]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for d in days:
        if out and out[-1][0][:7] == d[:7]:
            out[-1] = (d, closes[d])
        else:
            out.append((d, closes[d]))
    return out


def overlap_stats(etf_bars: list[Bar], proxy_bars: list[Bar]) -> Overlap | None:
    """Wie gut folgt die Ersatzreihe dem ETF? Monatsrenditen auf gemeinsamen Tagen."""
    e = {b.day: b.close for b in etf_bars}
    p = {b.day: b.close for b in proxy_bars}
    days = sorted(set(e) & set(p))
    me, mp = _month_ends(e, days), _month_ends(p, days)
    if len(me) < 13:
        return None
    re_ = [b[1] / a[1] - 1 for a, b in zip(me, me[1:])]
    rp = [b[1] / a[1] - 1 for a, b in zip(mp, mp[1:])]
    years = (dt.date.fromisoformat(days[-1]) - dt.date.fromisoformat(days[0])).days / 365.25
    cagr = lambda m: (m[-1][1] / m[0][1]) ** (1 / years) - 1  # noqa: E731
    te = statistics.pstdev([a - b for a, b in zip(re_, rp)]) * math.sqrt(12)
    return Overlap(days[0], days[-1], len(re_), statistics.correlation(re_, rp), cagr(me), cagr(mp), te)


def drift(bars: list[Bar], annual: float) -> list[Bar]:
    """Rendite einer Reihe um `annual` p.a. verschieben (Fassung C), pro Kalendertag verteilt."""
    if not bars or annual == 0:
        return list(bars)
    daily = math.log1p(annual) / 365.25
    t0 = dt.date.fromisoformat(bars[0].day)
    out = []
    for b in bars:
        f = math.exp(daily * (dt.date.fromisoformat(b.day) - t0).days)
        out.append(Bar(b.day, b.open * f, b.high * f, b.low * f, b.close * f))
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
    out = []
    for a, b in zip(bars, bars[1:]):
        r = b.close / a.close - 1
        if abs(r) > max_move:
            out.append(f"{b.day}: Tagesbewegung {r:+.1%}")
        gap = (dt.date.fromisoformat(b.day) - dt.date.fromisoformat(a.day)).days
        if gap > max_gap_days:
            out.append(f"{a.day} → {b.day}: Lücke von {gap} Tagen")
    return out


def chained_rates(parts: list[tuple[list[tuple[str, float]], str]]) -> list[tuple[str, float]]:
    """Mehrere Zinsreihen nacheinander: jede gilt ab ihrem Starttag (bis der nächste Teil beginnt)."""
    out: list[tuple[str, float]] = []
    starts = [s for _, s in parts] + ["9999-12-31"]
    for (rows, start), nxt in zip(parts, starts[1:]):
        out += [(d, v) for d, v in rows if start <= d < nxt]
    return out


Loader = Callable[[str], list[Bar]]
FredLoader = Callable[[str], list[tuple[str, float]]]


def build_asset(spec: AssetSpec, loader: Loader, variant: str = "A") -> ChainedSeries:
    if variant not in VARIANTS:
        raise ValueError(f"Fassung {variant!r} unbekannt (A, B, C)")
    etf_bars = loader(spec.etf.symbol)
    proxy_bars = None
    if spec.proxy is not None:
        if spec.proxy.weights:
            proxy_bars = blend_bars({s: loader(s) for s in spec.proxy.symbols}, dict(spec.proxy.weights))
        else:
            proxy_bars = loader(spec.proxy.symbol)
    overlap = overlap_stats(etf_bars, proxy_bars) if proxy_bars else None
    correction = 0.0
    if variant == "B":
        proxy_bars = None
    elif variant == "C" and proxy_bars and overlap is not None:
        correction = (1 + overlap.etf_cagr) / (1 + overlap.proxy_cagr) - 1
        proxy_bars = drift(proxy_bars, correction)
    label = spec.proxy.label if spec.proxy else None
    if label and correction:
        label += f" korr. {correction * 100:+.2f} % p.a."
    bars, segments = chain(etf_bars, proxy_bars, spec.etf.label, label)
    return ChainedSeries(spec.key, spec.name, bars, segments, quality_warnings(bars), overlap, correction)


@dataclass
class EtfUniverse:
    series: dict[str, ChainedSeries]
    rates: list[tuple[str, float]]  # T-Bill-Jahreszins als Dezimalzahl
    fx: list[tuple[str, float]]  # CHF pro USD
    chf_rates: list[tuple[str, float]]  # CHF-Kurzfristzins als Dezimalzahl
    variant: str = "A"
    snapshot: str = ""  # Name des Datenstands – steht in jedem Bericht
    missing: dict[str, str] = field(default_factory=dict)  # key -> Fehler

    def bars(self) -> dict[str, list[Bar]]:
        return {k: s.bars for k, s in self.series.items()}

    def full_start(self, keys: tuple[str, ...] = CORE_KEYS, warmup: int = 0) -> str | None:
        """Erster Tag, an dem alle `keys` mindestens `warmup` Handelstage Historie haben."""
        days = []
        for k in keys:
            s = self.series.get(k)
            if s is None or len(s.bars) <= warmup:
                return None
            days.append(s.bars[warmup].day)
        return max(days)

    def header(self) -> str:
        return f"{self.snapshot} | Fassung {self.variant}: {VARIANTS[self.variant]}"


def load_universe(loader: Loader, fred: FredLoader, specs: tuple[AssetSpec, ...] = ALL_ASSETS,
                  variant: str = "A", snapshot: str = "") -> EtfUniverse:
    series, missing = {}, {}
    for spec in specs:
        try:
            series[spec.key] = build_asset(spec, loader, variant)
        except (ValueError, KeyError, OSError) as e:
            missing[spec.key] = str(e)
    rates = [(b.day, b.close / 100) for b in loader(RATE_SYMBOL)]
    fx = fred(FX_SERIES)
    chf = chained_rates([([(d, v / 100) for d, v in fred(sid)], start) for sid, start in CHF_RATE_SERIES])
    return EtfUniverse(series, rates, fx, chf, variant, snapshot, missing)


def from_snapshot(snap: Snapshot, variant: str = "A", specs: tuple[AssetSpec, ...] = ALL_ASSETS) -> EtfUniverse:
    """Universum aus einem eingefrorenen Datenstand (der einzige Weg für Forschungsläufe)."""
    return load_universe(snap.yahoo, snap.fred, specs, variant, snap.name)


def available(universe: EtfUniverse, day: str, keys: tuple[str, ...] = CORE_KEYS, warmup: int = 0) -> list[str]:
    """Anlageklassen, die an `day` schon mindestens `warmup` Handelstage Historie haben."""
    out = []
    for k in keys:
        s = universe.series.get(k)
        if s is None:
            continue
        i = bisect.bisect_right([b.day for b in s.bars], day)
        if i > warmup:
            out.append(k)
    return out


def coverage_rows(universe: EtfUniverse, specs: tuple[AssetSpec, ...] = ALL_ASSETS) -> list[dict]:
    rows = []
    for spec in specs:
        s = universe.series.get(spec.key)
        if s is None:
            rows.append({"key": spec.key, "name": spec.name, "error": universe.missing.get(spec.key, "fehlt")})
            continue
        rows.append({
            "key": spec.key, "name": spec.name, "start": s.start, "end": s.bars[-1].day, "etf_start": s.etf_start,
            "chain": " → ".join(f"{seg.source} ({seg.start[:7]})" for seg in s.segments),
            "note": spec.note, "overlap": s.overlap, "warnings": s.warnings,
        })
    return rows
