"""Push-Nachrichten über einen selbst gehosteten ntfy-Server (https://ntfy.sh/docs/publish/).

Veröffentlicht als JSON an die Basis-URL – so funktionieren auch Umlaute im Titel. Fehler beim Senden
werden nur geloggt: Ein toter Push-Server darf nie den Handel stoppen.
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

PRIORITY = {"info": 3, "order": 3, "warn": 4, "error": 5}
TAGS = {"info": ["chart_with_upwards_trend"], "order": ["moneybag"], "warn": ["warning"], "error": ["rotating_light"]}


class Notifier:
    def __init__(self, url: str = "", topic: str = "quantdesk", token: str = "", session=None, timeout: float = 10.0):
        self.url = (url or "").rstrip("/")
        self.topic = topic
        self.token = token
        self._session = session or requests
        self.timeout = timeout
        self.sent: list[dict] = []  # für Tests und das Lauf-Protokoll
        self.failures = 0
        self.last_error = ""
        self._disabled = False  # nach 401/403 (Token fehlt/falsch) nichts mehr versuchen

    @classmethod
    def from_settings(cls, settings) -> "Notifier":
        return cls(settings.ntfy_url, settings.ntfy_topic, settings.ntfy_token)

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def send(self, title: str, message: str, level: str = "info", click: str | None = None) -> bool:
        payload = {"topic": self.topic, "title": title, "message": message,
                   "priority": PRIORITY.get(level, 3), "tags": TAGS.get(level, [])}
        if click:
            payload["click"] = click
        self.sent.append(payload)
        if not self.enabled:
            return False
        if self._disabled:
            self.failures += 1
            return False
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            resp = self._session.post(self.url, json=payload, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (401, 403):
                self._disabled = True
                hint = "NTFY_TOKEN fehlt oder ist falsch" if not self.token else "NTFY_TOKEN ungültig"
                log.warning("Push an ntfy abgelehnt (%s): %s – weitere Push-Versuche in diesem Lauf ausgelassen.",
                            status, hint)
            elif not self.failures:
                log.warning("Push an ntfy fehlgeschlagen: %s", e)  # nur der erste Fehler pro Lauf
            self.failures += 1
            self.last_error = str(e)[:200]
            return False
