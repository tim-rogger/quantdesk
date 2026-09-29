"""Yahoo-Finance-RSS-Schlagzeilen pro Symbol (Kontext für den AI Portfolio Manager)."""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import requests

from quantdesk.config import YAHOO_RSS_URL

log = logging.getLogger(__name__)

# Yahoo blockt den Default-User-Agent von requests gelegentlich
HEADERS = {"User-Agent": "Mozilla/5.0 (QuantDesk AI Bot)"}


def parse_rss(xml_text: str, limit: int = 5) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        items.append({"title": title, "published": (item.findtext("pubDate") or "").strip()})
        if len(items) >= limit:
            break
    return items


def fetch_headlines(symbol: str, limit: int = 5, session: requests.Session | None = None) -> list[dict]:
    """Nie eine Exception – bei Fehlern einfach leere Liste."""
    url = YAHOO_RSS_URL.format(symbol=symbol.upper())
    try:
        resp = (session or requests).get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("Yahoo-News für %s fehlgeschlagen: %s", symbol, e)
        return []
    return parse_rss(resp.text, limit)
