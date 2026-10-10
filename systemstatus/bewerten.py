"""Regeln: aus Rohdaten werden Kästen mit einer von vier Farben.

    gruen  läuft, Daten frisch
    gelb   läuft, aber etwas stimmt nicht
    rot    aus, abgestürzt, nicht erreichbar
    grau   noch nicht eingerichtet – kein Fehler, aber auch kein grün

Grundsatz: nie grün, wenn es nicht wirklich geprüft wurde. Fehlt eine Messung, wird es gelb oder rot.

Diese Datei macht keine Ein-/Ausgabe. Sie bekommt ein dict (aus messen.py) und gibt ein dict zurück,
das als system.json gespeichert wird. Deshalb lässt sich jede Regel mit einem Test prüfen.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from zoneinfo import ZoneInfo

# --- Schwellen (eine Stelle, damit man sie findet) -------------------------------------------------------
PLATTE_GELB = 80          # Prozent belegt
PLATTE_ROT = 95
RAM_GELB = 90             # Prozent belegt
NEUSTARTS_GELB = 2        # mehr als so viele Container-Neustarts in 24 h -> gelb
ZERTIFIKAT_GELB_TAGE = 14
NTFY_STILLE_TAGE = 7      # so lange keine Nachricht -> gelb (sonst weiss niemand, ob Push noch geht)
BACKUP_GELB_STUNDEN = 48
BACKUP_ROT_TAGE = 7
LAUF_KARENZ_MINUTEN = 45  # so lange nach dem geplanten Zeitpunkt muss ein Lauf im Protokoll stehen
IB_PRUEFUNG_MAX_ALTER = 10 * 60  # Sekunden; ältere Kontoprüfung zählt nicht mehr
PLATZHALTER_GRENZE = 1e300  # ib_async benutzt 1.8e308 als "kein Wert" – so etwas ist nie eine echte Zahl
PAPER_PRAEFIX = "DU"

GRUEN, GELB, ROT, GRAU = "gruen", "gelb", "rot", "grau"
_RANG = {GRUEN: 0, GRAU: 0, GELB: 1, ROT: 2}


class Befund:
    """Sammelt Gründe für einen Kasten. Die schlimmste Farbe gewinnt."""

    def __init__(self, stufe: str = GRUEN):
        self.stufe = stufe
        self.gruende: list[str] = []

    def gelb(self, grund: str) -> None:
        self._melde(GELB, grund)

    def rot(self, grund: str) -> None:
        self._melde(ROT, grund)

    def _melde(self, stufe: str, grund: str) -> None:
        self.gruende.append(grund)
        if _RANG[stufe] > _RANG[self.stufe] or self.stufe == GRAU:
            self.stufe = stufe


def zeile(titel: str, wert=None, *, zeit: float | None = None, zone: str | None = None) -> dict:
    """Eine Zeile im Detail-Kasten. Entweder ein Text (wert) oder ein Zeitpunkt (zeit, Unix-Sekunden).

    Zeitpunkte formatiert erst die Seite – in der Zeitzone des Handys, mit Zeitzonen-Kürzel. `zone` ist eine
    zweite Zeitzone, die zusätzlich angezeigt wird (z.B. New York bei Bot-Läufen).
    """
    if zeit is not None:
        out = {"t": titel, "z": zeit}
        if zone:
            out["zone"] = zone
        return out
    return {"t": titel, "w": "–" if wert is None else str(wert)}


def kasten(kid: str, titel: str, befund: Befund, kurz: str, zeilen: list[dict], **extra) -> dict:
    return {"id": kid, "titel": titel, "stufe": befund.stufe, "kurz": kurz, "gruende": befund.gruende,
            "zeilen": zeilen, **extra}


# ====================================================================== kleine Helfer
def ist_zahl(wert) -> bool:
    return isinstance(wert, (int, float)) and not isinstance(wert, bool)


def prozent(teil, ganz) -> float | None:
    if not ist_zahl(teil) or not ist_zahl(ganz) or ganz <= 0:
        return None
    return teil / ganz * 100


def gb(byte) -> str:
    return "–" if not ist_zahl(byte) else f"{byte / 1e9:.1f} GB"


def dauer(sekunden) -> str:
    """12345 -> "3 h 25 min"; 200000 -> "2 d 7 h"."""
    if not ist_zahl(sekunden) or sekunden < 0:
        return "–"
    minuten = int(sekunden // 60)
    tage, rest = divmod(minuten, 24 * 60)
    stunden, minuten = divmod(rest, 60)
    if tage:
        return f"{tage} d {stunden} h"
    if stunden:
        return f"{stunden} h {minuten} min"
    return f"{minuten} min"


def geld(wert, waehrung: str) -> str:
    """1234.5 -> "1'235 USD"; nan/inf/fehlend -> "–"."""
    if not ist_zahl(wert) or not math.isfinite(wert):
        return "–"
    return f"{wert:,.0f}".replace(",", "'") + f" {waehrung}"


def maskiere_konto(konto: str) -> str:
    """DUO844164 -> "DU…164". Die Prüfung auf DU läuft vorher auf der vollen Nummer."""
    konto = (konto or "").strip().upper()
    if len(konto) <= 5:
        return "…"
    vorne = 2 if konto.startswith(PAPER_PRAEFIX) else 1
    return konto[:vorne] + "…" + konto[-3:]


def ist_paper_konto(konto: str) -> bool:
    return (konto or "").strip().upper().startswith(PAPER_PRAEFIX)


def feld(daten, pfad: str):
    """Wert aus verschachteltem dict holen: feld(d, "c_account.invested")."""
    for teil in pfad.split("."):
        if not isinstance(daten, dict):
            return None
        daten = daten.get(teil)
    return daten


def kaputte_zahl(daten, pfad: str = "") -> str | None:
    """Pfad der ersten Zahl, die nan, inf oder ein Platzhalter (1.8e308) ist – sonst None."""
    if isinstance(daten, float) and (not math.isfinite(daten) or abs(daten) >= PLATZHALTER_GRENZE):
        return pfad or "Wert"
    if isinstance(daten, dict):
        for schluessel, wert in daten.items():
            treffer = kaputte_zahl(wert, f"{pfad}.{schluessel}" if pfad else str(schluessel))
            if treffer:
                return treffer
    if isinstance(daten, list):
        for i, wert in enumerate(daten):
            treffer = kaputte_zahl(wert, f"{pfad}[{i}]")
            if treffer:
                return treffer
    return None


def unix_zeit(wert) -> float | None:
    """Unix-Sekunden oder ISO-Text MIT Zeitzone -> Unix-Sekunden. Ohne Zeitzone: None (Zeit wäre geraten)."""
    if ist_zahl(wert) and math.isfinite(wert):
        return float(wert)
    if isinstance(wert, str):
        try:
            zeitpunkt = dt.datetime.fromisoformat(wert.replace("Z", "+00:00"))
        except ValueError:
            return None
        return zeitpunkt.timestamp() if zeitpunkt.tzinfo else None
    return None


# ====================================================================== Zeitplan der Bots
def _plan_zeiten(jetzt: float, uhrzeiten: list[str], zone: str, tage_zurueck: int, tage_vor: int) -> list[float]:
    """Alle geplanten Läufe (Mo–Fr) rund um `jetzt` als Unix-Sekunden, sortiert."""
    tz = ZoneInfo(zone)
    heute = dt.datetime.fromtimestamp(jetzt, tz).date()
    out = []
    for versatz in range(-tage_zurueck, tage_vor + 1):
        tag = heute + dt.timedelta(days=versatz)
        if tag.weekday() >= 5:  # Samstag, Sonntag
            continue
        for uhrzeit in uhrzeiten:
            stunde, minute = (int(x) for x in uhrzeit.split(":"))
            out.append(dt.datetime.combine(tag, dt.time(stunde, minute), tzinfo=tz).timestamp())
    return sorted(out)


def naechster_lauf(jetzt: float, uhrzeiten: list[str], zone: str) -> float | None:
    kuenftig = [t for t in _plan_zeiten(jetzt, uhrzeiten, zone, 0, 7) if t > jetzt]
    return kuenftig[0] if kuenftig else None


def faelliger_lauf(jetzt: float, uhrzeiten: list[str], zone: str) -> float | None:
    """Der letzte geplante Lauf, der inzwischen im Protokoll stehen müsste (Zeitpunkt + Karenz vorbei)."""
    grenze = jetzt - LAUF_KARENZ_MINUTEN * 60
    vergangen = [t for t in _plan_zeiten(jetzt, uhrzeiten, zone, 7, 0) if t <= grenze]
    return vergangen[-1] if vergangen else None


# ====================================================================== Server
def port22_offen(regeln: list[str]) -> bool:
    """Liest die Regel-Zeilen von `ufw status`. True, wenn Port 22 von ausserhalb Tailscale erlaubt ist.

    Zählt als offen: "22", "22/tcp", "OpenSSH", "ssh", eine Liste mit 22 – und "Anywhere" (alle Ports),
    wenn die Regel nicht nur für tailscale0 gilt. "LIMIT" ist auch erlaubt (nur gebremst).
    """
    for regel in regeln:
        aktion = "ALLOW" if " ALLOW" in regel else "LIMIT" if " LIMIT" in regel else None
        if not aktion:
            continue
        ziel = regel.split(aktion)[0].replace("(v6)", "").strip()
        if " on " in ziel:
            ziel, schnittstelle = (x.strip() for x in ziel.split(" on ", 1))
            if schnittstelle == "tailscale0":
                continue
        port = ziel.split("/")[0].strip().lower()
        if port in ("anywhere", "openssh", "ssh") or "22" in port.split(","):
            return True
    return False


def bewerte_server(s: dict) -> dict:
    b = Befund()
    platte = prozent(_minus(s.get("platte_gesamt"), s.get("platte_frei")), s.get("platte_gesamt"))
    ram = prozent(_minus(s.get("ram_gesamt"), s.get("ram_frei")), s.get("ram_gesamt"))
    last = s.get("last") or []
    kerne = s.get("kerne")
    ufw = s.get("ufw_aktiv")
    regeln = s.get("ufw_regeln") or []
    zone = s.get("zeitzone")

    if platte is None:
        b.gelb("Plattenbelegung unbekannt")
    elif platte > PLATTE_ROT:
        b.rot(f"Platte {platte:.0f} % voll")
    elif platte > PLATTE_GELB:
        b.gelb(f"Platte {platte:.0f} % voll")
    if ram is not None and ram > RAM_GELB:
        b.gelb(f"RAM {ram:.0f} % belegt")
    if len(last) == 3 and ist_zahl(kerne) and last[1] > kerne:
        b.gelb(f"Last {last[1]:.1f} höher als {kerne} Kerne")
    if ufw is False:
        b.rot("Firewall (ufw) ist aus")
    elif ufw is None:
        b.gelb("Firewall-Status unbekannt")
    offen = port22_offen(regeln) if ufw else None
    if offen:
        b.rot("Port 22 ist offen")
    if zone and zone not in ("UTC", "Etc/UTC"):
        b.gelb(f"Zeitzone ist {zone}, soll UTC sein")
    elif not zone:
        b.gelb("Zeitzone unbekannt")

    updates = s.get("sicherheitsupdates")
    zeilen = [
        zeile("CPU-Last (1 / 5 / 15 min)", " / ".join(f"{x:.2f}" for x in last) + f" · {kerne} Kerne" if len(last) == 3 else None),
        zeile("RAM belegt", f"{ram:.0f} % von {gb(s.get('ram_gesamt'))}" if ram is not None else None),
        zeile("Platte belegt", f"{platte:.0f} % · {gb(s.get('platte_frei'))} frei" if platte is not None else None),
        zeile("Läuft seit", dauer(s.get("uptime"))),
        zeile("Systemzeit", s.get("systemzeit")),
        zeile("Zeitzone", zone),
        zeile("Firewall (ufw)", {True: "aktiv", False: "AUS", None: "unbekannt"}[ufw]),
        zeile("Port 22 von aussen", {True: "OFFEN", False: "zu", None: "unbekannt"}[offen]),
        zeile("Sicherheitsupdates offen", updates if ist_zahl(updates) else "unbekannt"),
    ]
    kurz = f"Platte {platte:.0f} % · RAM {ram:.0f} %" if platte is not None and ram is not None else "Messung unvollständig"
    if b.gruende:
        kurz = b.gruende[0]
    return kasten("server", "Server quantdesk", b, kurz, zeilen)


def _minus(a, b):
    return a - b if ist_zahl(a) and ist_zahl(b) else None


# ====================================================================== Tailscale und Geräte
def bewerte_tailscale(t: dict, jetzt: float) -> dict:
    b = Befund()
    if t.get("verbunden") is not True:
        b.rot("nicht verbunden" if t.get("verbunden") is False else "Status nicht lesbar")
    ablauf = t.get("zertifikat_ablauf")
    if ablauf is None:
        b.gelb("HTTPS-Zertifikat nicht prüfbar")
    else:
        tage = (ablauf - jetzt) / 86400
        if tage < 0:
            b.rot("HTTPS-Zertifikat abgelaufen")
        elif tage < ZERTIFIKAT_GELB_TAGE:
            b.gelb(f"HTTPS-Zertifikat läuft in {tage:.0f} Tagen ab")
    zeilen = [
        zeile("Verbunden", {True: "ja", False: "NEIN"}.get(t.get("verbunden"), "unbekannt")),
        zeile("Name im Tailnet", t.get("hostname")),
        zeile("Letzter Handshake", zeit=t["letzter_handshake"]) if t.get("letzter_handshake") else zeile("Letzter Handshake", "keiner"),
        zeile("Zertifikat gültig bis", zeit=ablauf) if ablauf else zeile("Zertifikat gültig bis", "unbekannt"),
    ]
    kurz = b.gruende[0] if b.gruende else "verbunden"
    return kasten("tailscale", "Tailscale-Tunnel", b, kurz, zeilen)


def bewerte_geraet(art: str, geraete: list[dict]) -> dict:
    """iPhone/Laptop. Online = grün. Offline ist kein Fehler (Handy in der Tasche) – deshalb grau, nicht rot."""
    passend = [g for g in geraete if g.get("art") == art]
    online = [g for g in passend if g.get("online")]
    if online:
        b, kurz = Befund(), "online"
    elif passend:
        b, kurz = Befund(GRAU), "offline – kein Fehler"
    else:
        b, kurz = Befund(GRAU), "nicht im Tailnet"
    zuletzt = max((g.get("zuletzt") or 0 for g in passend), default=0)
    zeilen = [zeile("Im Tailnet", "ja" if passend else "nein"), zeile("Online", "ja" if online else "nein")]
    if zuletzt and not online:
        zeilen.append(zeile("Zuletzt gesehen", zeit=zuletzt))
    return kasten(art.lower(), art, b, kurz, zeilen)


# ====================================================================== Container
CONTAINER = ("bot", "ib-gateway", "ntfy", "dashboard")


def bewerte_container(name: str, c: dict | None, jetzt: float, zusatz: Befund | None = None,
                      zusatz_zeilen: list[dict] | None = None) -> dict:
    b = zusatz or Befund()
    if not c:
        b.rot("Container fehlt")
        return kasten(f"c-{name}", name, b, "Container fehlt", zusatz_zeilen or [])
    zustand = c.get("zustand") or "unbekannt"
    if zustand != "running":
        b.rot(f"Zustand: {zustand}")
    neustarts = c.get("neustarts_24h")
    if ist_zahl(neustarts) and neustarts > NEUSTARTS_GELB:
        b.gelb(f"{neustarts} Neustarts in 24 h")
    gestartet = c.get("gestartet")
    zeilen = [
        zeile("Zustand", zustand),
        zeile("Läuft seit", dauer(jetzt - gestartet) if gestartet and zustand == "running" else "–"),
        zeile("Neustarts in 24 h", neustarts if ist_zahl(neustarts) else "unbekannt"),
        zeile("Neustarts gesamt", c.get("neustarts_gesamt")),
        zeile("Image-Version", c.get("image_tag")),
    ] + (zusatz_zeilen or [])
    if b.gruende:
        kurz = b.gruende[0]
    else:
        kurz = f"läuft · {dauer(jetzt - gestartet)}" if gestartet else "läuft"
    return kasten(f"c-{name}", name, b, kurz, zeilen)


def bewerte_ntfy(c: dict | None, n: dict, jetzt: float) -> dict:
    b = Befund()
    if n.get("erreichbar") is not True:
        b.rot("ntfy nicht erreichbar")
    letzte = n.get("letzte_nachricht")
    if letzte is None:
        b.gelb("keine Nachricht bekannt")
    elif jetzt - letzte > NTFY_STILLE_TAGE * 86400:
        b.gelb(f"seit {dauer(jetzt - letzte)} keine Nachricht")
    zeilen = [zeile("Erreichbar", "ja" if n.get("erreichbar") else "NEIN"),
              zeile("Letzte Nachricht", zeit=letzte) if letzte else zeile("Letzte Nachricht", "unbekannt")]
    return bewerte_container("ntfy", c, jetzt, b, zeilen)


def bewerte_dashboard(c: dict | None, antwortet: bool | None, jetzt: float) -> dict:
    b = Befund()
    if antwortet is not True:
        b.rot("antwortet nicht")
    return bewerte_container("dashboard", c, jetzt, b, [zeile("Antwortet", "ja" if antwortet else "NEIN")])


# ====================================================================== IB Gateway, TWS-API, IBKR-Konto
def bewerte_ib(c: dict | None, ib: dict, jetzt: float) -> tuple[dict, dict, list[str]]:
    """Liefert (Kasten TWS-API, Kasten IBKR-Konto, Warnungen für ganz oben)."""
    warnungen = []
    api, konto = Befund(), Befund()
    geprueft = ib.get("geprueft")
    alt = not geprueft or jetzt - geprueft > IB_PRUEFUNG_MAX_ALTER
    konten = ib.get("konten")

    if not c or c.get("zustand") != "running":
        api.rot("Gateway-Container läuft nicht")
    if alt:
        api.gelb("Prüfung veraltet")
    if ib.get("port_offen") is False:
        api.rot("Port 4004 zu")
    elif ib.get("port_offen") and konten is None:
        api.rot("Port offen, Gateway antwortet nicht – nicht eingeloggt?")

    if konten is None:
        konto.rot("nicht eingeloggt – keine Kontonummer")
    elif not konten:
        konto.rot("keine Kontonummer gemeldet")
    else:
        fremd = [k for k in konten if not ist_paper_konto(k)]
        if fremd:
            konto.rot("kein Paper-Konto")
            warnungen.append(f"IBKR meldet {maskiere_konto(fremd[0])} – kein Paper-Konto! Nichts handeln lassen, "
                             "Gateway-Login prüfen.")
    if alt and konten is not None:
        konto.gelb("Prüfung veraltet")

    maskiert = ", ".join(maskiere_konto(k) for k in konten) if konten else "–"
    zeitzeile = zeile("Geprüft", zeit=geprueft) if geprueft else zeile("Geprüft", "noch nie")
    api_zeilen = [zeile("Port 4004", {True: "offen", False: "ZU"}.get(ib.get("port_offen"), "unbekannt")),
                  zeile("Eingeloggt", "ja" if konten else "NEIN"), zeitzeile]
    if ib.get("fehler"):
        api_zeilen.append(zeile("Meldung", ib["fehler"]))
    paper = bool(konten) and all(ist_paper_konto(k) for k in konten)
    konto_zeilen = [zeile("Konto", maskiert), zeile("Modus", "Paper" if paper else "NICHT Paper" if konten else "–"),
                    zeitzeile]
    api_kasten = kasten("tws-api", "TWS-API :4004", api, api.gruende[0] if api.gruende else "antwortet", api_zeilen)
    konto_kurz = konto.gruende[0] if konto.gruende else f"{maskiert} · Paper"
    konto_kasten = kasten("ibkr", "IBKR Paper-Konto", konto, konto_kurz, konto_zeilen)
    return api_kasten, konto_kasten, warnungen


# ====================================================================== Bots
def bewerte_bot(eintrag: dict, daten: dict, jetzt: float) -> dict:
    """Ein Bot aus der Registry (deploy/bots.toml). `daten` = {"status": dict|None, "laeufe": [dict], "fehler": str}."""
    kid = "bot-" + str(eintrag.get("id", "?"))
    name = str(eintrag.get("name", "Bot"))
    waehrung = str(eintrag.get("waehrung", "USD"))
    zone = str(eintrag.get("zeitzone", "UTC"))
    uhrzeiten = list(eintrag.get("laeufe", []))
    if not eintrag.get("eingerichtet", False):
        return kasten(kid, name, Befund(GRAU), "noch nicht eingerichtet",
                      [zeile("Strategie", eintrag.get("strategie"))], modus=None)

    b = Befund()
    status = daten.get("status")
    laeufe = daten.get("laeufe") or []
    if daten.get("fehler"):
        b.rot(daten["fehler"])
    kaputt = kaputte_zahl(status) or kaputte_zahl(laeufe[-1] if laeufe else None)
    if kaputt:
        b.rot(f"Zahlen enthalten nan/inf ({kaputt})")

    modus = feld(status, eintrag.get("modus_feld", "mode"))
    investiert = feld(status, eintrag.get("investiert_feld", "c_account.invested"))
    positionen = feld(status, eintrag.get("positionen_feld", "c_account.positions"))

    letzter = laeufe[-1] if laeufe else None
    lauf_zeit = unix_zeit(letzter.get(eintrag.get("lauf_zeit_feld", "ts"))) if letzter else None
    fehler = (letzter or {}).get(eintrag.get("lauf_fehler_feld", "errors")) or []
    fehler = fehler if isinstance(fehler, list) else [fehler]
    ok = (letzter or {}).get(eintrag.get("lauf_ok_feld", "ok"))
    if letzter is None:
        b.gelb("noch kein Lauf im Protokoll")
    elif lauf_zeit is None:
        b.gelb("Zeitpunkt des letzten Laufs ohne Zeitzone")
    if letzter is not None and ok is not True:
        b.rot("letzter Lauf gescheitert" + (f": {fehler[0]}" if fehler else ""))
    elif fehler:
        b.gelb(f"{len(fehler)} Fehler im letzten Lauf")
    faellig = faelliger_lauf(jetzt, uhrzeiten, zone) if uhrzeiten else None
    if faellig and lauf_zeit and lauf_zeit < faellig - 300:
        b.gelb("geplanter Lauf fehlt")

    naechster = naechster_lauf(jetzt, uhrzeiten, zone) if uhrzeiten else None
    anzahl = len(positionen) if isinstance(positionen, (list, dict)) else positionen if ist_zahl(positionen) else None
    zeilen = [
        zeile("Strategie", eintrag.get("strategie")),
        zeile("Modus", modus),
        zeile("Letzter Lauf", zeit=lauf_zeit, zone=zone) if lauf_zeit else zeile("Letzter Lauf", "–"),
        zeile("Ergebnis", "ok" if ok is True else "gescheitert" if letzter else "–"),
        zeile("Fehler im letzten Lauf", len(fehler)),
    ] + [zeile("Fehler", text) for text in fehler[:3]] + [
        zeile("Geplant", ", ".join(uhrzeiten) + f" ({zone}, Mo–Fr, Feiertage nicht berücksichtigt)" if uhrzeiten else "–"),
        zeile("Nächster Lauf", zeit=naechster, zone=zone) if naechster else zeile("Nächster Lauf", "–"),
        zeile("Investiert", geld(investiert, waehrung)),
        zeile("Offene Positionen", anzahl),
    ]
    if b.gruende:
        kurz = b.gruende[0]
    else:
        kurz = f"{geld(investiert, waehrung)} · {anzahl} Pos."
    return kasten(kid, name, b, kurz, zeilen, modus=str(modus) if modus else None, letzter_lauf=lauf_zeit,
                  lauf_ok=ok is True if letzter else None)


# ====================================================================== Backup und Totmannschalter
def bewerte_backup(bk: dict, jetzt: float) -> dict:
    if not bk.get("eingerichtet"):
        return kasten("backup", "Backup (Backblaze B2)", Befund(GRAU), "noch nicht eingerichtet",
                      [zeile("Eingerichtet", "nein – RESTIC_REPOSITORY in .env leer")])
    b = Befund()
    letzter = bk.get("letzter_snapshot")
    if letzter is None:
        b.gelb("noch kein Snapshot gesehen")
    elif jetzt - letzter > BACKUP_ROT_TAGE * 86400:
        b.rot(f"letzter Snapshot vor {dauer(jetzt - letzter)}")
    elif jetzt - letzter > BACKUP_GELB_STUNDEN * 3600:
        b.gelb(f"letzter Snapshot vor {dauer(jetzt - letzter)}")
    zeilen = [zeile("Eingerichtet", "ja"),
              zeile("Letzter Snapshot", zeit=letzter) if letzter else zeile("Letzter Snapshot", "keiner in den Logs")]
    kurz = b.gruende[0] if b.gruende else f"vor {dauer(jetzt - letzter)}"
    return kasten("backup", "Backup (Backblaze B2)", b, kurz, zeilen)


def bewerte_totmann(tm: dict) -> dict:
    if not tm.get("eingerichtet"):
        return kasten("totmann", "Totmannschalter (Healthchecks.io)", Befund(GRAU), "noch nicht eingerichtet",
                      [zeile("Eingerichtet", "nein – HEALTHCHECKS_URL in .env leer")])
    b = Befund()
    if tm.get("ping_fehler") is None:
        b.gelb("Ping-Ergebnis unbekannt")
    elif tm["ping_fehler"]:
        b.gelb("Ping an Healthchecks.io fehlgeschlagen")
    versuch = tm.get("letzter_versuch")
    zeilen = [zeile("Eingerichtet", "ja"),
              zeile("Letzter Ping-Versuch", zeit=versuch) if versuch else zeile("Letzter Ping-Versuch", "unbekannt"),
              zeile("Ping-Fehler in 48 h", {True: "ja", False: "keine", None: "unbekannt"}[tm.get("ping_fehler")])]
    return kasten("totmann", "Totmannschalter (Healthchecks.io)", b, b.gruende[0] if b.gruende else "pingt", zeilen)


# ====================================================================== alles zusammen
def bewerten(roh: dict) -> dict:
    """Rohdaten (messen.sammeln) -> Inhalt von system.json."""
    jetzt = roh["jetzt"]
    tailscale = roh.get("tailscale") or {}
    container = roh.get("container") or {}
    api, konto, warnungen = bewerte_ib(container.get("ib-gateway"), roh.get("ib") or {}, jetzt)
    kaesten = [
        bewerte_geraet("iPhone", tailscale.get("geraete") or []),
        bewerte_geraet("Laptop", tailscale.get("geraete") or []),
        bewerte_tailscale(tailscale, jetzt),
        bewerte_server(roh.get("server") or {}),
        bewerte_ntfy(container.get("ntfy"), roh.get("ntfy") or {}, jetzt),
        bewerte_dashboard(container.get("dashboard"), roh.get("dashboard_antwortet"), jetzt),
        bewerte_container("bot", container.get("bot"), jetzt),
        bewerte_container("ib-gateway", container.get("ib-gateway"), jetzt),
        api,
        konto,
    ]
    kaesten += [bewerte_bot(b["eintrag"], b, jetzt) for b in roh.get("bots") or []]
    kaesten += [bewerte_backup(roh.get("backup") or {}, jetzt), bewerte_totmann(roh.get("totmann") or {})]
    zaehler = {stufe: sum(k["stufe"] == stufe for k in kaesten) for stufe in (GRUEN, GELB, ROT, GRAU)}
    return entschaerfen({
        "version": 1,
        "erzeugt": jetzt,
        "erzeugt_utc": dt.datetime.fromtimestamp(jetzt, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "warnungen": warnungen,
        "zaehler": zaehler,
        "kaesten": kaesten,
    })


# ====================================================================== nichts Geheimes auf die Seite
_GEHEIM = [
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "‹IP›"),
    (re.compile(r"\b[0-9a-f]{1,4}(?::[0-9a-f]{0,4}){4,7}\b", re.I), "‹IP›"),
    (re.compile(r"/(?:home|root|var|etc|opt|srv|mnt|run)/\S*"), "‹Pfad›"),
    (re.compile(r"\btk_[A-Za-z0-9]+"), "‹Token›"),
    (re.compile(r"sk-ant-\S+"), "‹Schlüssel›"),
    (re.compile(r"hc-ping\.com/\S+"), "hc-ping.com/‹…›"),
    (re.compile(r"[\w.-]+\.ts\.net\b"), "‹tailnet›"),
]
_KONTO = re.compile(r"\b(DU|U)(?=[A-Z0-9]*\d)[A-Z0-9]{4,}\b")


def entschaerfen(daten):
    """Zweite Sicherung: jede Zeichenkette in system.json wird nach IPs, Pfaden, Tokens und Kontonummern
    durchsucht und maskiert. Die erste Sicherung ist, so etwas gar nicht erst einzutragen.
    Nebenbei: nan/inf werden zu None – system.json enthält nie ungültiges JSON."""
    if isinstance(daten, str):
        for muster, ersatz in _GEHEIM:
            daten = muster.sub(ersatz, daten)
        return _KONTO.sub(lambda m: maskiere_konto(m.group(0)), daten)
    if isinstance(daten, float) and not math.isfinite(daten):
        return None
    if isinstance(daten, dict):
        return {k: entschaerfen(v) for k, v in daten.items()}
    if isinstance(daten, list):
        return [entschaerfen(v) for v in daten]
    return daten
