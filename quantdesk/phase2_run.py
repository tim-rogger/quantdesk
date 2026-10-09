"""Ein angemeldeter Kandidat auf einem Datenstand: Fassungen A/B/C + Nebenauswertung, Urteil, Register.

Läuft nur mit bestätigter Anmeldung (quantdesk.trials.require_confirmed). Zeitraum und Zählweise von N
kommen aus der Anmeldung bzw. stehen hier fest und werden nach einem Lauf nicht geändert.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from quantdesk import etf_strategies as es
from quantdesk import trials
from quantdesk.allocation import Aligned, Costs, SimResult, simulate
from quantdesk.etf_data import CORE_KEYS, EtfUniverse
from quantdesk.phase2 import Market, Normalized, Verdict, correlation, normalized, verdict

MAIN_START = {"A": "2004-01-02", "C": "2004-01-02"}  # B: erster Tag mit allen 9 echten ETFs + 1 Jahr Vorlauf
SIDE_START = "1996-01-02"  # Nebenauswertung (Fassung A, wachsendes Universum) – entscheidet nie
CURRENCIES = ("USD", "CHF")
CAPITAL = 100_000.0
COSTS = Costs(commission=1.0, slippage_bps=5.0)
CANDIDATES = {"D": es.candidates_d}
N_MODE = re.compile(r"^\*\*Zählweise N:\*\*\s*(streng|effektiv)\b", re.M)


def n_mode(name: str, root: str = trials.REGISTRATIONS) -> str:
    with open(os.path.join(root, f"{name}.md"), encoding="utf-8") as f:
        m = N_MODE.search(f.read())
    if not m:
        raise PermissionError(f"Anmeldung {name}: Zählweise N fehlt ('**Zählweise N:** streng' oder 'effektiv').")
    return m.group(1)


def n_trials(mode: str, registry: str = trials.REGISTRY) -> int:
    strict, effective = trials.counts(trials.read(registry))
    return strict if mode == "streng" else effective


@dataclass
class Evaluation:
    label: str  # z.B. "A ab 2004", "Neben: A ab 1996, wachsendes Universum"
    universe: EtfUniverse
    start: str
    end: str
    results: dict[str, SimResult]
    candidates: list[str]
    benchmarks: list[str]
    verdicts: dict[tuple[str, str], Verdict] = field(default_factory=dict)
    normalized: dict[str, Normalized] = field(default_factory=dict)

    @property
    def spy(self) -> SimResult:
        return self.results[self.benchmarks[0]]

    def passed(self, name: str) -> bool:
        return all(self.verdicts[(name, c)].passed for c in CURRENCIES)

    def correlations(self) -> dict[tuple[str, str], float]:
        names = self.candidates + self.benchmarks
        return {(a, b): correlation(self.results[a], self.results[b]) for a in names for b in names}


def evaluate(universe: EtfUniverse, label: str, start: str, n: int, candidate_fn=es.candidates_d,
             keys: tuple[str, ...] = CORE_KEYS) -> Evaluation:
    bars = {k: universe.series[k].bars for k in keys if k in universe.series}
    data = Aligned(bars, universe.rates)
    end = min(s.bars[-1].day for k, s in universe.series.items() if k in keys)
    cands, benches = candidate_fn(keys), es.benchmarks(keys)
    results = {s.name: simulate(s, data, start, end, CAPITAL, COSTS) for s in cands + benches}
    ev = Evaluation(label, universe, start, end, results, [s.name for s in cands], [s.name for s in benches])
    market = Market(universe.rates, universe.fx, universe.chf_rates)
    for name in ev.candidates:
        for cur in CURRENCIES:
            ev.verdicts[(name, cur)] = verdict(results[name], ev.spy, market, n, cur)
        ev.normalized[name] = normalized(results[name], ev.spy, market)
    return ev


def run(name: str, universes: dict[str, EtfUniverse], reg_root: str = trials.REGISTRATIONS,
        registry: str = trials.REGISTRY) -> tuple[list[Evaluation], int, str]:
    """Kandidat `name` in allen Fassungen. `universes` = {"A": …, "B": …, "C": …} aus demselben Datenstand."""
    trials.require_confirmed(name, reg_root)
    if len({u.snapshot for u in universes.values()}) != 1:
        raise ValueError("Alle Fassungen müssen aus demselben Datenstand stammen.")
    mode = n_mode(name, reg_root)
    n = n_trials(mode, registry)
    fn = CANDIDATES[name]
    b_start = universes["B"].full_start(CORE_KEYS, warmup=es.LONG + 1)
    if b_start is None:
        raise ValueError("Fassung B: nicht alle Anlageklassen vorhanden.")
    evals = [
        evaluate(universes["A"], f"A – verkettet, ab {MAIN_START['A'][:4]}", MAIN_START["A"], n, fn),
        evaluate(universes["B"], f"B – nur echte ETFs, ab {b_start[:7]}", b_start, n, fn),
        evaluate(universes["C"], f"C – Ersatz korrigiert, ab {MAIN_START['C'][:4]}", MAIN_START["C"], n, fn),
        evaluate(universes["A"], f"Neben – A ab {SIDE_START[:4]}, wachsendes Universum (entscheidet nicht)",
                 SIDE_START, n, fn),
    ]
    return evals, n, mode


def final(evals: list[Evaluation], name: str) -> tuple[bool, str]:
    """Urteil Sicht 1: A und B; weichen sie ab, gilt B (Regel aus research/DATEN.md)."""
    a, b = evals[0].passed(name), evals[1].passed(name)
    if a == b:
        return b, "A und B einig"
    return b, f"A {'bestanden' if a else 'durchgefallen'}, B {'bestanden' if b else 'durchgefallen'} → B gilt"
