"""Tages-Snapshot und Status fürs Dashboard (data/snapshots.jsonl, data/status.json, data/runs.jsonl).

Der Bot schreibt diese Dateien nach jedem Lauf. Das Dashboard liest sie nur – es spricht nie selbst mit
IBKR oder Yahoo und kann deshalb nichts kaputt machen.
"""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import time
from collections import defaultdict

from quantdesk.forward import C_PARAMS
from quantdesk.journal import Fill

STOP_FILE = "STOP"


def data_path(settings, name: str) -> str:
    os.makedirs(settings.data_dir, exist_ok=True)
    return os.path.join(settings.data_dir, name)


def stop_active(settings) -> bool:
    return os.path.exists(os.path.join(settings.data_dir, STOP_FILE))


def save_json(path: str, data) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, default=str)
    os.replace(tmp, path)


def append_jsonl(path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def read_jsonl(path: str, limit: int | None = None) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return rows[-limit:] if limit else rows


def c_budget(systems) -> float:
    """Budget von C: (Levels + 1) × Orderbetrag für jedes handelbare C-System."""
    return sum((s.num_levels + 1) * s.order_usd for s in systems
               if s.order_usd and s.trend_exit and not s.not_tradable)


def virtual_account(fills: list[Fill], prices: dict[str, float], budget: float, fee: float = 1.0) -> dict:
    """C als eigenes Konto: Budget - Käufe + Verkäufe - Gebühren + eigene Positionen zum letzten Kurs."""
    qty: dict[str, float] = defaultdict(float)
    cost: dict[str, float] = defaultdict(float)
    cash = budget
    for f in sorted(fills, key=lambda x: x.ts):
        if f.side == "BUY":
            qty[f.symbol] += f.qty
            cost[f.symbol] += f.qty * f.price
            cash -= f.qty * f.price + fee
        else:
            if qty[f.symbol] > 0:
                cost[f.symbol] *= max(qty[f.symbol] - f.qty, 0) / qty[f.symbol]
            qty[f.symbol] -= f.qty
            cash += f.qty * f.price - fee
    positions = {}
    invested = 0.0
    for sym, q in qty.items():
        if q <= 1e-9:
            continue
        price = prices.get(sym)
        avg = cost[sym] / q
        value = q * price if price else q * avg
        invested += value
        positions[sym] = {"qty": q, "avg": round(avg, 4), "price": price, "value": round(value, 2),
                          "pnl": round(value - cost[sym], 2), "pnl_pct": round(value / cost[sym] - 1, 4) if cost[sym] else 0}
    return {"budget": budget, "cash": round(cash, 2), "invested": round(invested, 2),
            "value": round(cash + invested, 2), "positions": positions}


def write_snapshot(path: str, snap: dict) -> None:
    """Ein Snapshot pro Tag (ein späterer Lauf am selben Tag ersetzt den früheren)."""
    rows = [r for r in read_jsonl(path) if r.get("day") != snap["day"]]
    rows.append(snap)
    rows.sort(key=lambda r: r["day"])
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    os.replace(tmp, path)


def _perf(p) -> dict:
    return {k: round(v, 4) if isinstance(v, float) else v for k, v in dataclasses.asdict(p).items()}


def report_dict(report) -> dict | None:
    if report is None:
        return None
    return {
        "start": report.start, "end": report.end, "trading_days": report.trading_days,
        "months_done": report.months_done, "finished": report.finished, "passed": report.passed,
        "estimated_fills": report.estimated_fills,
        "rows": {name: {cur: _perf(p) for cur, p in per.items()} for name, per in report.rows.items()},
        "monthly": report.monthly,
        "checks": [dataclasses.asdict(c) for c in report.checks],
        "curves": report.curves or {},
    }


def systems_dict(engine) -> list[dict]:
    out = []
    for s in engine.systems.values():
        q = engine.quotes.get(s.symbol)
        own = s.bot_qty()
        cost = (s.entry_qty or 0) * (s.entry_price or 0) + sum((lv.qty or 0) * lv.price for lv in s.levels if lv.status == "filled")
        avg = cost / (own + s.sold_qty) if own + s.sold_qty else None
        price = q.price if q else None
        out.append({
            "symbol": s.symbol, "status": s.status, "trend": s.trend_label, "bot_qty": own,
            "broker_qty": s.position, "entry_price": s.entry_price, "avg": round(avg, 2) if avg else None,
            "price": price, "price_source": q.source if q else None,
            "pnl": round((price - avg) * own, 2) if price and avg and own else 0.0,
            "open_levels": [{"level": lv.level, "price": lv.price} for lv in s.levels if lv.status == "placed"],
            "filled_levels": sum(1 for lv in s.levels if lv.status == "filled"),
            "entry_pending": bool(s.entry_order_id), "exit_pending": bool(s.exit_order_id),
            "not_tradable": s.not_tradable, "closed": s.closed,
        })
    return sorted(out, key=lambda r: (r["closed"] is not None, r["not_tradable"], -r["bot_qty"], r["symbol"]))


def build_status(engine, settings, fills: list[Fill], net_liq: float | None, report=None,
                 runs: list[dict] | None = None, now: float | None = None) -> dict:
    now = now or time.time()
    systems = list(engine.systems.values())
    prices = {sym: q.price for sym, q in engine.quotes.items()}
    account = virtual_account(fills, prices, c_budget(systems))
    return {
        "generated_at": now,
        "mode": settings.mode, "broker": settings.broker, "account_id": settings.ibkr_account_id,
        "stop_active": stop_active(settings),
        "strategy": f"Kandidat C: {C_PARAMS.label()}",
        "c_account": account,
        "net_liquidation": net_liq,
        "systems": systems_dict(engine),
        "counts": {"systems": len(systems), "on": sum(s.is_on and s.tradable for s in systems),
                   "not_tradable": [s.symbol for s in systems if s.not_tradable],
                   "closed": {s.symbol: s.closed for s in systems if s.closed}},
        "report": report_dict(report),
        "runs": runs or [],
    }
