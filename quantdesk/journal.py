"""Handelsjournal: jede ausgeführte Order als eine JSON-Zeile – Grundlage für den Monatsreport."""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from dataclasses import asdict, dataclass

log = logging.getLogger(__name__)
MAX_QTY = 1e7
MAX_PRICE = 1e6


def valid_fill(qty, price) -> bool:
    """Plausible Menge und Preis (endlich, > 0, unter den Grenzen) – sonst gehört der Eintrag nicht ins Journal."""
    try:
        q, p = float(qty), float(price)
    except (TypeError, ValueError):
        return False
    return math.isfinite(q) and math.isfinite(p) and 0 < q < MAX_QTY and 0 < p < MAX_PRICE


@dataclass(frozen=True)
class Fill:
    ts: float
    day: str  # YYYY-MM-DD (UTC)
    symbol: str
    side: str  # BUY / SELL
    qty: float
    price: float
    kind: str  # entry | level | exit | adopted
    order_id: str
    simulated: bool = False
    estimated: bool = False  # Tag/Preis geschätzt (Ausführung bei IBKR nicht mehr abrufbar)
    exec_ids: tuple = ()  # IBKR-Ausführungs-IDs, aus denen der Eintrag entstand
    note: str = ""


class Journal:
    def __init__(self, path: str, clock=time.time):
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()

    def record(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        kind: str,
        order_id: str,
        simulated: bool,
        when: float | None = None,
        estimated: bool = False,
        exec_ids: tuple | list = (),
        note: str = "",
    ) -> Fill:
        """`when` = Ausführungszeit (Unix-Sekunden); ohne Angabe: jetzt. Unplausible Werte -> ValueError."""
        if not valid_fill(qty, price):
            raise ValueError(f"{symbol}: unplausibler Fill (Menge {qty!r}, Preis {price!r}) – nicht gebucht")
        ts = self._clock() if when is None else when
        fill = Fill(ts, time.strftime("%Y-%m-%d", time.gmtime(ts)), symbol, side, float(qty), float(price),
                    kind, str(order_id), simulated, estimated, tuple(exec_ids), note)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(fill), allow_nan=False) + "\n")
        return fill

    def order_ids(self) -> set[str]:
        return {f.order_id for f in self.read(include_simulated=True)}

    def read(self, include_simulated: bool = False) -> list[Fill]:
        """Alle gültigen Einträge. Unplausible (z.B. Menge 1.8e308) werden übersprungen und in .invalid gemerkt."""
        self.invalid: list[dict] = []
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    d = json.loads(line)
                    if not valid_fill(d.get("qty"), d.get("price")):
                        self.invalid.append(d)
                        continue
                    d["exec_ids"] = tuple(d.get("exec_ids") or ())
                    fill = Fill(**d)
                    if include_simulated or not fill.simulated:
                        out.append(fill)
        if self.invalid:
            log.warning("Journal: %s unplausible Einträge übersprungen (forward_test.py migrate bereinigt sie)",
                        len(self.invalid))
        return out

    def rewrite(self, fills: list[Fill]) -> None:
        """Journal atomar neu schreiben (nur für die Migration/Bereinigung)."""
        tmp = self.path + ".tmp"
        with self._lock:
            with open(tmp, "w", encoding="utf-8") as f:
                for fill in fills:
                    f.write(json.dumps(asdict(fill), allow_nan=False) + "\n")
            os.replace(tmp, self.path)
