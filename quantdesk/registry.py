"""Register aller Orders, die der Bot selbst platziert hat (bot_orders.jsonl).

Nur Ausführungen dieser Orders zählen zu den Systemen des Bots. Käufe oder Verkäufe, die Tim von Hand
im selben Symbol macht, werden nie gebucht, übernommen oder verkauft.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass

ENTRY, LEVEL, EXIT = "entry", "level", "exit"


@dataclass(frozen=True)
class BotOrder:
    order_id: str
    symbol: str
    kind: str  # entry | level | exit
    side: str  # BUY | SELL
    qty: float
    level: int | None = None
    price: float | None = None  # Limitpreis bei Levels
    ts: float | None = None


class OrderRegistry:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._cache: dict[str, BotOrder] | None = None

    def all(self) -> dict[str, BotOrder]:
        with self._lock:
            if self._cache is None:
                self._cache = {}
                if os.path.exists(self.path):
                    with open(self.path, encoding="utf-8") as f:
                        for line in f:
                            if line.strip():
                                o = BotOrder(**json.loads(line))
                                self._cache[o.order_id] = o
            return dict(self._cache)

    def get(self, order_id: str | None) -> BotOrder | None:
        return self.all().get(str(order_id)) if order_id else None

    def add(self, order: BotOrder) -> bool:
        """Neue Order merken (idempotent). True = neu."""
        known = self.all()
        if order.order_id in known:
            return False
        with self._lock:
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(order)) + "\n")
            self._cache[order.order_id] = order
        return True


# Zeilen in quantdesk.log, mit denen der Bot jede platzierte Order meldet
_LOG_PATTERNS = (
    (ENTRY, "BUY", re.compile(r"(?P<sym>[A-Z.]+): Einstieg Market-Buy (?P<qty>\d+) platziert – ID (?P<id>[\w-]+)\.")),
    (LEVEL, "BUY", re.compile(
        r"(?P<sym>[A-Z.]+): Limit-Buy (?P<qty>\d+) @ (?P<price>[\d.]+) \(Level (?P<level>\d+)\) platziert – ID (?P<id>[\w-]+)\.")),
    (EXIT, "SELL", re.compile(r"(?P<sym>[A-Z.]+): Trend unter SMA\d+ – Market-Sell (?P<qty>\d+) platziert – ID (?P<id>[\w-]+)\.")),
)


def orders_from_log(lines) -> list[BotOrder]:
    """Platzierte Orders aus quantdesk.log rekonstruieren (für Orders aus der Zeit vor dem Register)."""
    out = []
    for line in lines:
        for kind, side, rx in _LOG_PATTERNS:
            m = rx.search(line)
            if m and not m.group("id").startswith("DRY-"):
                d = m.groupdict()
                out.append(BotOrder(d["id"], d["sym"], kind, side, float(d["qty"]),
                                    int(d["level"]) if d.get("level") else None,
                                    float(d["price"]) if d.get("price") else None))
                break
    return out


_REJECT = re.compile(r"(?P<sym>[A-Z.]+): IBKR hat die Order abgelehnt: (?P<msg>.+)")


def not_tradable_from_log(lines) -> set[str]:
    """Symbole, deren Orders IBKR wegen fehlender Handelsberechtigung abgelehnt hat."""
    from quantdesk.broker.base import is_permission_error

    out = set()
    for line in lines:
        m = _REJECT.search(line)
        if m and is_permission_error(m.group("msg")):
            out.add(m.group("sym"))
    return out
