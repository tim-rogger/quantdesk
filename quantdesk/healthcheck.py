"""Überwachung von aussen über Healthchecks.io (https://healthchecks.io/docs/http_api/).

Nach jedem erfolgreichen Handelslauf ein Ping an HEALTHCHECKS_URL, bei Fehler an .../fail. Bleibt der Ping aus
(Server tot, Docker weg, Scheduler hängt), alarmiert Healthchecks.io selbst – das kann der Server nicht.
Ein toter Healthchecks-Dienst darf den Bot nie stören: Fehler werden nur geloggt.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)


def ping(status: str = "success", message: str = "", url: str | None = None, session=None, timeout: float = 10) -> bool:
    """status: 'success' | 'fail' | 'start'. Ohne HEALTHCHECKS_URL passiert nichts."""
    url = (url if url is not None else os.getenv("HEALTHCHECKS_URL", "")).strip().rstrip("/")
    if not url:
        return False
    target = url if status == "success" else f"{url}/{status}"
    try:
        resp = (session or requests).post(target, data=message[:10000].encode("utf-8"), timeout=timeout)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning("Healthchecks-Ping fehlgeschlagen: %s", e)
        return False
