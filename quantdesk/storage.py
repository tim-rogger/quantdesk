"""equities.json laden/speichern – atomar, damit ein Absturz die Datei nie halb schreibt."""
from __future__ import annotations

import json
import os
import tempfile
import time

from quantdesk.strategy import EquitySystem


class StorageError(Exception):
    pass


def load(path: str) -> dict[str, EquitySystem]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        systems = [EquitySystem.from_dict(d) for d in raw.get("systems", [])]
    except (json.JSONDecodeError, ValueError, KeyError, TypeError, AttributeError) as e:
        backup = f"{os.path.splitext(path)[0]}.{time.strftime('%Y%m%d-%H%M%S')}.corrupt.json"
        os.replace(path, backup)
        raise StorageError(f"{path} war ungültig ({e}) und wurde nach {backup} verschoben. Start mit leerer Liste.") from e
    return {s.symbol: s for s in systems}


def save(path: str, systems: dict[str, EquitySystem]) -> None:
    data = {"version": 1, "systems": [s.to_dict() for s in systems.values()]}
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".equities-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, allow_nan=False)  # nie NaN/Infinity in die Datei
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
