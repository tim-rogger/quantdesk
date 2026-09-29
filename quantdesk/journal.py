"""Handelsjournal: jede ausgeführte Order als eine JSON-Zeile – Grundlage für den Monatsreport."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass


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


class Journal:
    def __init__(self, path: str, clock=time.time):
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()

    def record(self, symbol: str, side: str, qty: float, price: float, kind: str, order_id: str, simulated: bool) -> Fill:
        now = self._clock()
        fill = Fill(now, time.strftime("%Y-%m-%d", time.gmtime(now)), symbol, side, float(qty), float(price),
                    kind, str(order_id), simulated)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(fill)) + "\n")
        return fill

    def read(self, include_simulated: bool = False) -> list[Fill]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    fill = Fill(**json.loads(line))
                    if include_simulated or not fill.simulated:
                        out.append(fill)
        return out
