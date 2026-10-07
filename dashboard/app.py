"""QuantDesk-Dashboard: kleine Web-App (PWA) – nur lesend, einzige Aktion ist STOP-ALL mit PIN.

Liest data/status.json und data/snapshots.jsonl, die run_daily.py nach jedem Lauf schreibt.
Spricht nie selbst mit IBKR. Erreichbar nur über Tailscale (`tailscale serve`), nicht öffentlich.

Start lokal:  uvicorn dashboard.app:create_app --factory --port 8000
"""
from __future__ import annotations

import hmac
import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from quantdesk.config import load_settings
from quantdesk.notify import Notifier
from quantdesk.status import STOP_FILE, read_jsonl

STATIC = Path(__file__).parent / "static"
MAX_PIN_FAILS = 5
LOCKOUT_SECONDS = 15 * 60


class StopRequest(BaseModel):
    pin: str


class PinGuard:
    """Nach 5 falschen PINs 15 Minuten gesperrt (pro Client-Adresse)."""

    def __init__(self, clock=time.time):
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def locked(self, client: str) -> bool:
        now = self._clock()
        with self._lock:
            recent = [t for t in self._fails.get(client, []) if now - t < LOCKOUT_SECONDS]
            self._fails[client] = recent
            return len(recent) >= MAX_PIN_FAILS

    def fail(self, client: str) -> None:
        with self._lock:
            self._fails.setdefault(client, []).append(self._clock())

    def reset(self, client: str) -> None:
        with self._lock:
            self._fails.pop(client, None)


def create_app(settings=None, notifier: Notifier | None = None) -> FastAPI:
    settings = settings or load_settings()
    notifier = notifier or Notifier.from_settings(settings)
    data_dir = Path(settings.data_dir)
    guard = PinGuard()
    app = FastAPI(title="QuantDesk", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/status")
    def status() -> JSONResponse:
        path = data_dir / "status.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"missing": True}
        stop = data_dir / STOP_FILE
        data["stop_active"] = stop.exists()
        data["stop_since"] = stop.stat().st_mtime if stop.exists() else None
        data["snapshots"] = read_jsonl(str(data_dir / "snapshots.jsonl"))
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.post("/api/stop")
    def stop_all(req: StopRequest, request: Request) -> dict:
        client = request.client.host if request.client else "?"
        if not settings.dashboard_pin:
            raise HTTPException(403, "Keine PIN gesetzt (DASHBOARD_PIN in .env) – STOP-ALL ist deaktiviert.")
        if guard.locked(client):
            raise HTTPException(429, "Zu viele falsche PINs – 15 Minuten gesperrt.")
        if not hmac.compare_digest(req.pin.strip().encode(), settings.dashboard_pin.encode()):
            guard.fail(client)
            raise HTTPException(403, "Falsche PIN.")
        guard.reset(client)
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / STOP_FILE).write_text(json.dumps({"ts": time.time(), "by": f"dashboard {client}"}), encoding="utf-8")
        notifier.send("QuantDesk: STOP-ALL ausgelöst",
                      "Über das Dashboard. Beim nächsten Lauf werden alle Systeme ausgeschaltet; offene Orders bleiben "
                      "bei IBKR – bei Bedarf im Portal stornieren.", "error")
        return {"ok": True, "message": "STOP-ALL aktiv. Der Bot handelt ab dem nächsten Lauf nicht mehr."}

    @app.get("/healthz")
    def health() -> dict:
        return {"ok": True}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest")
    def manifest() -> FileResponse:
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js")
    def service_worker() -> FileResponse:
        # Service-Worker muss im Wurzelpfad liegen, damit er die ganze App abdeckt
        return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
