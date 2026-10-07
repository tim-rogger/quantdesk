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
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            resp = self._session.post(self.url, json=payload, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            log.warning("Push an ntfy fehlgeschlagen: %s", e)
            return False
