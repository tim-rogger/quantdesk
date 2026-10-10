"""Lotse – Push-Nachrichten über ntfy, eigenes Thema "lotse". Wirft nie: Fehlt der Push, läuft der Lauf weiter.

Adresse und Token kommen aus der Umgebung (NTFY_URL, NTFY_TOKEN), nie aus config.toml oder dem Code.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)


class Push:
    def __init__(self, url: str = "", thema: str = "lotse", token: str = "", session=None):
        self.url, self.thema, self.token = url.rstrip("/"), thema, token
        self._session = session or requests
        self.gesendet: list[dict] = []
        self.fehler = 0
        self._aus = False  # nach 401/403 in diesem Lauf nicht mehr versuchen

    @classmethod
    def aus_umgebung(cls, thema: str) -> "Push":
        return cls(os.getenv("NTFY_URL", ""), thema, os.getenv("NTFY_TOKEN", ""))

    def sende(self, titel: str, text: str, wichtig: bool = False) -> bool:
        nachricht = {"topic": self.thema, "title": titel, "message": text, "priority": 4 if wichtig else 3,
                     "tags": ["warning"] if wichtig else ["chart_with_upwards_trend"]}
        self.gesendet.append(nachricht)
        if not self.url or self._aus:
            return False
        kopf = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            antwort = self._session.post(self.url, json=nachricht, headers=kopf, timeout=10)
            antwort.raise_for_status()
            return True
        except requests.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (401, 403):
                self._aus = True
                log.warning("ntfy lehnt ab (%s) – NTFY_TOKEN prüfen. Weitere Pushes in diesem Lauf ausgelassen.", status)
            elif not self.fehler:
                log.warning("Push an ntfy fehlgeschlagen: %s", e)
            self.fehler += 1
            return False
