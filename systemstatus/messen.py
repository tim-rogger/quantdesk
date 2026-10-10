"""Rohdaten holen – alles, was mit dem Server spricht, steht hier.

Jede Messung fängt ihre Fehler selbst ab und liefert dann None ("unbekannt"). Eine kaputte Messung darf
nie den ganzen Sammler anhalten. Bewertet (grün/gelb/rot) wird hier nichts – das macht bewerten.py.

Geschrieben wird nur in deploy/state/system/ (system.json und merker.json). Alles andere wird nur gelesen.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import time
import tomllib
import urllib.request

from systemstatus import tws

COMPOSE_PROJEKT = "quantdesk"
DASHBOARD_URL = "http://127.0.0.1:8080/healthz"   # wie in docker-compose.yml veröffentlicht
NTFY_URL = "http://127.0.0.1:8090/v1/health"
TWS_PORT = 4004
BOT_CONTAINER = "bot"


# ====================================================================== Helfer
def befehl(argumente: list[str], timeout: float = 15, stderr: bool = False) -> str | None:
    """Programm ausführen, Ausgabe als Text. Fehlt das Programm oder scheitert es: None."""
    try:
        p = subprocess.run(argumente, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 and not stderr:
        return None
    return p.stderr if stderr else p.stdout


def iso_zu_unix(text: str | None) -> float | None:
    """RFC-3339-Zeit von Docker/Tailscale ("2026-10-10T13:05:00.123456789Z") -> Unix-Sekunden."""
    if not text or text.startswith("0001-01-01"):
        return None
    text = re.sub(r"(\.\d{6})\d+", r"\1", text.strip()).replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def http_ok(url: str, timeout: float = 5) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as antwort:
            return antwort.read(10000) if antwort.status == 200 else None
    except (OSError, ValueError):
        return None


def env_gesetzt(pfad: str, schluessel: str) -> bool:
    """Steht in der .env-Datei ein Wert für `schluessel`? Der Wert selbst wird nie zurückgegeben."""
    try:
        with open(pfad, encoding="utf-8") as f:
            for z in f:
                name, _, wert = z.strip().partition("=")
                if name.strip() == schluessel:
                    return bool(wert.strip().strip('"').strip("'"))
    except OSError:
        pass
    return False


def lies_jsonl_ende(pfad: str, anzahl: int = 50) -> list[dict]:
    """Die letzten `anzahl` Zeilen einer JSONL-Datei. Kaputte Zeilen werden übersprungen."""
    with open(pfad, encoding="utf-8") as f:
        zeilen = f.readlines()[-anzahl:]
    out = []
    for z in zeilen:
        try:
            wert = json.loads(z)
        except ValueError:
            continue
        if isinstance(wert, dict):
            out.append(wert)
    return out


# ====================================================================== Server
def server() -> dict:
    out: dict = {"kerne": os.cpu_count()}
    try:
        with open("/proc/loadavg", encoding="ascii") as f:
            out["last"] = [float(x) for x in f.read().split()[:3]]
    except (OSError, ValueError):
        out["last"] = None
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            info = {z.split(":")[0]: int(z.split()[1]) * 1024 for z in f if ":" in z}
        out["ram_gesamt"], out["ram_frei"] = info.get("MemTotal"), info.get("MemAvailable")
    except (OSError, ValueError, IndexError):
        pass
    try:
        platte = shutil.disk_usage("/")
        out["platte_gesamt"], out["platte_frei"] = platte.total, platte.free
    except OSError:
        pass
    try:
        with open("/proc/uptime", encoding="ascii") as f:
            out["uptime"] = float(f.read().split()[0])
    except (OSError, ValueError):
        pass
    out["systemzeit"] = dt.datetime.now(dt.timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC")
    zone = befehl(["timedatectl", "show", "-p", "Timezone", "--value"])
    out["zeitzone"] = zone.strip() if zone else None
    ufw = befehl(["ufw", "status"])
    if ufw is None:
        out["ufw_aktiv"], out["ufw_regeln"] = None, []
    else:
        out["ufw_aktiv"] = "Status: active" in ufw
        out["ufw_regeln"] = [z for z in ufw.splitlines() if " ALLOW" in z or " LIMIT" in z]
    return out


def sicherheitsupdates() -> int | None:
    """Ubuntu: apt-check schreibt "alle;sicherheit" nach stderr. Langsam – deshalb nur alle 30 min."""
    text = befehl(["/usr/lib/update-notifier/apt-check"], timeout=120, stderr=True)
    m = re.search(r"(\d+);(\d+)", text or "")
    return int(m.group(2)) if m else None


# ====================================================================== Tailscale
def tailscale() -> dict:
    text = befehl(["tailscale", "status", "--json"])
    if text is None:
        return {"verbunden": None}
    try:
        status = json.loads(text)
    except ValueError:
        return {"verbunden": None}
    selbst = status.get("Self") or {}
    geraete, handshakes = [], []
    for peer in (status.get("Peer") or {}).values():
        art = {"ios": "iPhone", "windows": "Laptop", "macos": "Laptop"}.get(str(peer.get("OS", "")).lower())
        handshake = iso_zu_unix(peer.get("LastHandshake"))
        if handshake:
            handshakes.append(handshake)
        if art:
            geraete.append({"art": art, "online": bool(peer.get("Online")), "zuletzt": iso_zu_unix(peer.get("LastSeen"))})
    return {
        "verbunden": status.get("BackendState") == "Running" and bool(selbst.get("Online", True)),
        "hostname": selbst.get("HostName"),
        "letzter_handshake": max(handshakes) if handshakes else None,
        "geraete": geraete,
        "_dns": (selbst.get("DNSName") or "").rstrip("."),
        "_ip": (selbst.get("TailscaleIPs") or [None])[0],
    }


def zertifikat_ablauf(dns: str, ip: str | None) -> float | None:
    """Das HTTPS-Zertifikat holen, das `tailscale serve` gerade ausliefert, und sein Ablaufdatum lesen."""
    if not dns or not ip:
        return None
    try:
        kontext = ssl.create_default_context()
        with socket.create_connection((ip, 443), timeout=5) as roh:
            with kontext.wrap_socket(roh, server_hostname=dns) as tls:
                return ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
    except (OSError, ssl.SSLError, KeyError, ValueError):
        return None


# ====================================================================== Docker
def container(merker: dict, jetzt: float) -> dict:
    """Zustand der Compose-Container. Neustarts in 24 h zählt der Sammler selbst (siehe neustarts_zaehlen)."""
    namen = befehl(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={COMPOSE_PROJEKT}",
                    "--format", "{{.Names}}"])
    if not namen or not namen.split():
        return {}
    text = befehl(["docker", "inspect", *namen.split()])
    try:
        details = json.loads(text or "[]")
    except ValueError:
        return {}
    out = {}
    for c in details:
        dienst = (c.get("Config", {}).get("Labels") or {}).get("com.docker.compose.service")
        if not dienst:
            continue
        zustand = c.get("State", {})
        image = c.get("Config", {}).get("Image", "")
        netze = (c.get("NetworkSettings") or {}).get("Networks") or {}
        out[dienst] = {
            "zustand": zustand.get("Status"),
            "gestartet": iso_zu_unix(zustand.get("StartedAt")),
            "neustarts_gesamt": c.get("RestartCount", 0),
            "neustarts_24h": neustarts_zaehlen(merker, dienst, c.get("Id", ""), c.get("RestartCount", 0), jetzt),
            "image_tag": image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else "latest",
            "_ip": next((n.get("IPAddress") for n in netze.values() if n.get("IPAddress")), None),
        }
    return out


def neustarts_zaehlen(merker: dict, dienst: str, cid: str, zaehler: int, jetzt: float) -> int:
    """Docker zählt automatische Neustarts (RestartCount), aber nicht wann. Der Sammler merkt sich deshalb
    jedes Mal, wenn der Zähler steigt, die Uhrzeit (in merker.json). Wird der Container neu erstellt
    (`docker compose up -d` nach Update), beginnt Docker wieder bei 0 – dann fangen wir auch neu an."""
    eintrag = merker.setdefault("neustarts", {}).setdefault(dienst, {"id": cid, "zaehler": zaehler, "zeiten": []})
    if eintrag["id"] != cid:
        eintrag.update(id=cid, zaehler=zaehler)
    elif zaehler > eintrag["zaehler"]:
        eintrag["zeiten"] += [jetzt] * (zaehler - eintrag["zaehler"])
        eintrag["zaehler"] = zaehler
    eintrag["zeiten"] = [t for t in eintrag["zeiten"] if jetzt - t < 86400]
    return len(eintrag["zeiten"])


def bot_logs(stunden: int) -> str:
    """Logs des Bot-Containers der letzten Stunden, jede Zeile mit UTC-Zeitstempel von Docker."""
    name = befehl(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={COMPOSE_PROJEKT}",
                   "--filter", f"label=com.docker.compose.service={BOT_CONTAINER}", "--format", "{{.Names}}"])
    if not name or not name.split():
        return ""
    p = None
    try:
        p = subprocess.run(["docker", "logs", "--timestamps", "--since", f"{stunden}h", name.split()[0]],
                           capture_output=True, text=True, timeout=30, errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (p.stdout or "") + (p.stderr or "")


# ====================================================================== Dienste
def dashboard_antwortet() -> bool:
    return http_ok(DASHBOARD_URL) is not None


def ntfy(ntfy_daten: str, merker: dict) -> dict:
    """Erreichbar? Und wann ging die letzte Nachricht raus?

    ntfy hält Nachrichten nur 12 h in seiner cache.db. Damit "seit 7 Tagen keine Nachricht" trotzdem
    erkannt wird, merkt sich der Sammler die jüngste gesehene Nachricht in merker.json.
    """
    antwort = http_ok(NTFY_URL)
    try:
        erreichbar = bool(antwort) and json.loads(antwort).get("healthy") is True
    except ValueError:
        erreichbar = False
    pfad = os.path.join(ntfy_daten, "cache.db")
    try:
        with sqlite3.connect(f"file:{pfad}?mode=ro", uri=True, timeout=2) as db:
            neueste = db.execute("SELECT MAX(time) FROM messages").fetchone()[0]
        if neueste and neueste > merker.get("ntfy_letzte", 0):
            merker["ntfy_letzte"] = float(neueste)
    except sqlite3.Error:
        pass
    return {"erreichbar": erreichbar, "letzte_nachricht": merker.get("ntfy_letzte")}


def ib_gateway(ip: str | None) -> dict:
    """Konten beim Gateway abfragen. Der Port ist nicht veröffentlicht – vom Host aus erreicht man ihn
    über die interne Docker-Adresse des Containers."""
    if not ip:
        return {"port_offen": False, "konten": None, "fehler": "Container hat keine Adresse", "geprueft": time.time()}
    return tws.konten_abfragen(ip, TWS_PORT) | {"geprueft": time.time()}


# ====================================================================== Bots aus der Registry
def registry(pfad: str) -> list[dict]:
    with open(pfad, "rb") as f:
        return list(tomllib.load(f).get("bot", []))


def bot(state: str, eintrag: dict) -> dict:
    """Status- und Lauf-Datei eines Bots lesen (nur lesen!). nan/inf werden hier NICHT entfernt –
    bewerten.py soll sie sehen und rot melden."""
    out: dict = {"eintrag": eintrag, "status": None, "laeufe": [], "fehler": ""}
    if not eintrag.get("eingerichtet"):
        return out
    status_pfad = os.path.join(state, eintrag.get("status_datei", ""))
    laeufe_pfad = os.path.join(state, eintrag.get("laeufe_datei", ""))
    try:
        with open(status_pfad, encoding="utf-8") as f:
            out["status"] = json.load(f)
    except FileNotFoundError:
        out["fehler"] = "Statusdatei fehlt"
    except (OSError, ValueError):
        out["fehler"] = "Statusdatei nicht lesbar"
    try:
        out["laeufe"] = lies_jsonl_ende(laeufe_pfad)
    except FileNotFoundError:
        pass
    except OSError:
        out["fehler"] = out["fehler"] or "Lauf-Protokoll nicht lesbar"
    return out


# ====================================================================== Backup und Totmannschalter
SNAPSHOT = re.compile(r"^(\S+) .*\bsnapshot [0-9a-f]{8,} saved", re.M)
PING_FEHLER = "Healthchecks-Ping fehlgeschlagen"


def backup(env_pfad: str, logs: str) -> dict:
    """restic meldet nach jedem Backup "snapshot 1a2b3c4d saved". Diese Zeile suchen wir im Bot-Log –
    so braucht der Sammler keinen Zugang zu Backblaze."""
    zeiten = [iso_zu_unix(m.group(1)) for m in SNAPSHOT.finditer(logs)]
    zeiten = [z for z in zeiten if z]
    return {"eingerichtet": env_gesetzt(env_pfad, "RESTIC_REPOSITORY"), "letzter_snapshot": max(zeiten, default=None)}


def totmann(env_pfad: str, logs: str, laeufe: list[dict]) -> dict:
    """Der Bot pingt Healthchecks.io nach jedem Handelslauf. Klappt das nicht, steht eine Warnung im Log."""
    handel = [r.get("ts") for r in laeufe if r.get("mode") == "trade" and isinstance(r.get("ts"), (int, float))]
    return {"eingerichtet": env_gesetzt(env_pfad, "HEALTHCHECKS_URL"),
            "ping_fehler": (PING_FEHLER in logs) if logs else None,
            "letzter_versuch": max(handel, default=None)}


# ====================================================================== alles
class Zwischenspeicher:
    """Langsame Messungen nicht alle 30 s wiederholen."""

    def __init__(self, uhr=time.monotonic):
        self._werte: dict = {}
        self._uhr = uhr

    def hole(self, name: str, alle_sekunden: float, funktion):
        jetzt = self._uhr()
        if name in self._werte and jetzt - self._werte[name][0] < alle_sekunden:
            return self._werte[name][1]
        wert = funktion()
        self._werte[name] = (jetzt, wert)
        return wert


def sammeln(deploy: str, speicher: Zwischenspeicher, merker: dict) -> dict:
    jetzt = time.time()
    state = os.path.join(deploy, "state")
    env_pfad = os.path.join(deploy, ".env")
    srv = server()
    srv["sicherheitsupdates"] = speicher.hole("updates", 30 * 60, sicherheitsupdates)
    ts = tailscale()
    ts["zertifikat_ablauf"] = speicher.hole("zertifikat", 60 * 60, lambda: zertifikat_ablauf(ts["_dns"], ts["_ip"])) \
        if ts.get("_dns") else None
    con = container(merker, jetzt)
    gateway_ip = (con.get("ib-gateway") or {}).get("_ip")
    ib = speicher.hole("ib", 2 * 60, lambda: ib_gateway(gateway_ip))
    eintraege = registry(os.path.join(deploy, "bots.toml"))
    bots = [bot(state, e) for e in eintraege]
    logs_8_tage = speicher.hole("logs", 10 * 60, lambda: bot_logs(8 * 24))
    logs_48_h = "\n".join(z for z in logs_8_tage.splitlines()
                          if (iso_zu_unix(z.split(" ", 1)[0]) or 0) > jetzt - 48 * 3600)
    c_laeufe = next((b["laeufe"] for b in bots if b["eintrag"].get("id") == "c"), [])
    return {
        "jetzt": jetzt,
        "server": srv,
        "tailscale": ts,
        "container": con,
        "dashboard_antwortet": dashboard_antwortet(),
        "ntfy": ntfy(os.path.join(deploy, "ntfy-data"), merker),
        "ib": ib,
        "bots": bots,
        "backup": backup(env_pfad, logs_8_tage),
        "totmann": totmann(env_pfad, logs_48_h, c_laeufe),
    }
