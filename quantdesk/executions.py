"""Archiv aller gesehenen IBKR-Ausführungen (executions.jsonl).

IBKR liefert Ausführungen nur 7 Tage zurück. Der Bot speichert jede gesehene Ausführung dauerhaft,
damit Fills von Orders aus früheren Sitzungen auch später noch gebucht werden können.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass

from quantdesk.broker.base import Execution


@dataclass(frozen=True)
class OrderFill:
    """Alle Ausführungen einer Order zusammengefasst."""

    order_id: str
    symbol: str
    side: str
    qty: float
    price: float  # mengengewichteter Durchschnitt
    ts: float  # Zeit der letzten Teilausführung
    exec_ids: tuple = ()


def aggregate(executions: list[Execution]) -> dict[str, OrderFill]:
    groups: dict[str, list[Execution]] = {}
    for e in executions:
        groups.setdefault(e.order_id, []).append(e)
    out = {}
    for oid, ex in groups.items():
        qty = sum(e.qty for e in ex)
        if qty <= 0:
            continue
        price = sum(e.qty * e.price for e in ex) / qty
        out[oid] = OrderFill(oid, ex[0].symbol, ex[0].side, qty, price, max(e.ts for e in ex),
                             tuple(e.exec_id for e in ex))
    return out


class ExecutionArchive:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> list[Execution]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [Execution(**json.loads(line)) for line in f if line.strip()]

    def merge(self, executions: list[Execution]) -> list[Execution]:
        """Neue Ausführungen (nach exec_id) anhängen; liefert das ganze Archiv."""
        with self._lock:
            known = self.load()
            seen = {e.exec_id for e in known}
            new = [e for e in executions if e.exec_id not in seen]
            if new:
                with open(self.path, "a", encoding="utf-8") as f:
                    for e in new:
                        f.write(json.dumps(e.to_dict()) + "\n")
            return known + new
