"""Register aller getesteten Strategien (research/registry.md) und Anmeldungen (research/anmeldungen/).

Liest und ergänzt die Markdown-Tabelle, zählt N für die Mehrfachtest-Korrektur und prüft, ob ein Kandidat
von Tim bestätigt angemeldet ist, bevor er laufen darf.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

REGISTRY = os.path.join("research", "registry.md")
REGISTRATIONS = os.path.join("research", "anmeldungen")
COUNTED = ("getestet", "angemeldet", "läuft")
COLUMNS = ("Nr", "Datum", "Phase", "Strategie", "Parameter", "Daten", "Versuche neu", "Status", "Ergebnis")


@dataclass
class Trial:
    nr: int
    date: str
    phase: str
    name: str
    params: str
    data: str
    new_trials: int
    status: str
    result: str

    def row(self) -> str:
        cells = [str(self.nr), self.date, self.phase, self.name, self.params, self.data, str(self.new_trials),
                 self.status, self.result]
        return "| " + " | ".join(c.replace("|", "/") for c in cells) + " |"


ROW = re.compile(r"^\|\s*(\d+)\s*\|")


def read(path: str = REGISTRY) -> list[Trial]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not ROW.match(line):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != len(COLUMNS):
                raise ValueError(f"Register: Zeile hat {len(cells)} statt {len(COLUMNS)} Spalten: {line.strip()}")
            out.append(Trial(int(cells[0]), cells[1], cells[2], cells[3], cells[4], cells[5], int(cells[6]),
                             cells[7], cells[8]))
    nrs = [t.nr for t in out]
    if nrs != list(range(1, len(nrs) + 1)):
        raise ValueError("Register: Nummern müssen lückenlos ab 1 laufen")
    return out


ETF_PHASE = "2"  # Varianten auf einem ETF-Datenstand (Forschungsphase 2 und später)


def counts(trials: list[Trial]) -> tuple[int, int]:
    """(N streng über alles, Anzahl Strategievarianten über alles) – zur Information."""
    counted = [t for t in trials if t.status in COUNTED]
    return sum(t.new_trials for t in counted), len(counted)


def etf_counter(trials: list[Trial]) -> int:
    """N effektiv (Tims Festlegung 09.10.2026): laufender Zähler aller Varianten, die je auf einem
    ETF-Datenstand angemeldet bzw. gerechnet wurden. Zeilen werden nie gelöscht -> der Zähler sinkt nie."""
    return sum(1 for t in trials if t.phase == ETF_PHASE and t.status != "nicht getestet")


def append(trial: Trial, path: str = REGISTRY) -> Trial:
    """Neue Zeile anhängen (Nummer wird vergeben). Zeilen werden nie gelöscht."""
    trial.nr = len(read(path)) + 1
    with open(path, "rb") as f:
        raw = f.read()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8")
    if not text.endswith(("\n", "\r\n")):
        text += nl
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text + trial.row() + nl)
    return trial


def set_result(nr: int, status: str, result: str, path: str = REGISTRY) -> None:
    """Status/Ergebnis einer Zeile eintragen (z.B. nach dem Lauf)."""
    with open(path, encoding="utf-8", newline="") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        m = ROW.match(line)
        if m and int(m.group(1)) == nr:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            t = Trial(int(cells[0]), *cells[1:6], int(cells[6]), status, result)
            ending = "\r\n" if line.endswith("\r\n") else "\n"
            lines[i] = t.row() + ending
            break
    else:
        raise KeyError(f"Register: Nr. {nr} nicht gefunden")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)


STATUS = re.compile(r"^\*\*Status:\*\*\s*(\S+)", re.M)


def registration_status(name: str, root: str = REGISTRATIONS) -> str:
    """Status einer Anmeldung: 'ENTWURF', 'BESTÄTIGT' … (aus der Zeile '**Status:** …')."""
    path = os.path.join(root, f"{name}.md")
    if not os.path.exists(path):
        return "FEHLT"
    with open(path, encoding="utf-8") as f:
        m = STATUS.search(f.read())
    return m.group(1).strip() if m else "UNBEKANNT"


def require_confirmed(name: str, root: str = REGISTRATIONS) -> None:
    status = registration_status(name, root)
    if status != "BESTÄTIGT":
        raise PermissionError(f"Kandidat {name}: Anmeldung ist '{status}' – ein Lauf auf echten Daten ist erst nach "
                              f"Tims Bestätigung erlaubt ({root}/{name}.md, Zeile '**Status:** BESTÄTIGT').")
