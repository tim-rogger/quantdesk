"""Das IB Gateway fragen: "Welche Konten verwaltest du?" – ohne ib_async, nur mit der Standardbibliothek.

Das ist der kleinste Teil des TWS-API-Protokolls, der dafür nötig ist:

    1. TCP-Verbindung zu ib-gateway:4004
    2. Begrüssung:  "API\\0" + Länge + "v100..176"   (welche Protokoll-Versionen wir können)
    3. Antwort des Gateways: seine Version und Uhrzeit
    4. "startApi" mit einer EIGENEN Client-ID (Bot C hat 17, Lotse 27 – wir nehmen 91)
    5. Das Gateway schickt von sich aus "managedAccounts" (Nachricht Nr. 15) mit den Kontonummern
    6. Verbindung schliessen

Es werden keine Orders, keine Kurse, nichts Schreibendes angefragt. Jede Nachricht ist:
4 Byte Länge (big endian) + Felder, jedes Feld mit \\0 abgeschlossen.
"""
from __future__ import annotations

import socket
import struct
import time

CLIENT_ID = 91
VERSIONEN = b"v100..176"
START_API = "71"
MANAGED_ACCOUNTS = "15"


def nachricht(felder: list[str]) -> bytes:
    """Felder -> Bytes mit Längen-Präfix, so wie das Gateway sie erwartet."""
    inhalt = "".join(f + "\0" for f in felder).encode("ascii")
    return struct.pack(">I", len(inhalt)) + inhalt


def _genau(sock: socket.socket, anzahl: int) -> bytes:
    daten = b""
    while len(daten) < anzahl:
        stueck = sock.recv(anzahl - len(daten))
        if not stueck:
            raise ConnectionError("Verbindung vom Gateway geschlossen")
        daten += stueck
    return daten


def lies_nachricht(sock: socket.socket) -> list[str]:
    laenge = struct.unpack(">I", _genau(sock, 4))[0]
    if laenge > 1_000_000:
        raise ValueError("unplausible Nachrichtenlänge")
    text = _genau(sock, laenge).decode("utf-8", errors="replace")
    return text.split("\0")[:-1]


def konten_abfragen(host: str, port: int, client_id: int = CLIENT_ID, timeout: float = 5.0) -> dict:
    """Ergebnis: {"port_offen": bool, "konten": [..] oder None, "fehler": Text}.

    port_offen=True und konten=None heisst: es nimmt jemand ab (socat im Container), aber das Gateway
    selbst antwortet nicht – meistens, weil es (noch) nicht eingeloggt ist.
    """
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError:
        return {"port_offen": False, "konten": None, "fehler": "keine TCP-Verbindung"}
    with sock:
        try:
            sock.settimeout(timeout)
            sock.sendall(b"API\0" + struct.pack(">I", len(VERSIONEN)) + VERSIONEN)
            lies_nachricht(sock)  # Version und Uhrzeit des Gateways – brauchen wir nicht
            sock.sendall(nachricht([START_API, "2", str(client_id), ""]))
            ende = time.monotonic() + timeout
            while time.monotonic() < ende:
                felder = lies_nachricht(sock)
                if len(felder) >= 3 and felder[0] == MANAGED_ACCOUNTS:
                    konten = [k.strip().upper() for k in felder[2].split(",") if k.strip()]
                    return {"port_offen": True, "konten": konten, "fehler": ""}
            return {"port_offen": True, "konten": None, "fehler": "keine Kontonummer gemeldet"}
        except (OSError, ValueError, struct.error):
            return {"port_offen": True, "konten": None, "fehler": "Gateway antwortet nicht"}
