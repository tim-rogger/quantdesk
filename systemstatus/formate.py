"""Lauf-Protokolle verschiedener Bots in eine gemeinsame Form bringen.

bewerten.py kennt nur eine Form: ein Status-dict und eine Liste von Läufen {"ts", "ok", "errors"}.
Schreibt ein Bot sein Protokoll anders, übersetzt eine Funktion hier. In der Registry (deploy/bots.toml)
steht dann `format = "..."`. Ein weiterer Bot im selben Format braucht wieder nur einen Registry-Eintrag.

Reine Funktionen, keine Dateien – messen.py liest, hier wird nur übersetzt.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo


def zeit_lesen(text, zone_wenn_fehlt: str | None) -> float | None:
    """ISO-Zeit -> Unix-Sekunden. Ohne Zeitzone nur, wenn die Registry sagt, welche gemeint ist."""
    if not isinstance(text, str):
        return None
    try:
        zeitpunkt = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if zeitpunkt.tzinfo is None:
        if not zone_wenn_fehlt:
            return None
        zeitpunkt = zeitpunkt.replace(tzinfo=ZoneInfo(zone_wenn_fehlt))
    return zeitpunkt.timestamp()


def lotse_tagebuch(eintraege: list[dict], ausfuehrungen: list[dict], jetzt: float,
                   zone_wenn_fehlt: str | None, laeuft_hoechstens_minuten: float) -> tuple[dict, list[dict]]:
    """Lotses laeufe.jsonl (+ ausfuehrungen.jsonl) -> (Status, Läufe) in der gemeinsamen Form.

    Lotse schreibt pro Lauf mehrere Zeilen mit derselben Lauf-Nummer:
        start  (zeit, modus)   – immer
        kasse  (investiert)    – nur wenn der Lauf bis zum Schluss durchkam
        ende   (ergebnis-Text) – fast immer; fehlt, wenn der Prozess abgestürzt oder abgebrochen ist
    Daraus:
        ok      = es gibt eine kasse-Zeile zu diesem Lauf (genau dann schreibt Lotse sie)
        Fehler  = der ergebnis-Text, wenn nicht ok; "kein Ende" bei start ohne ende
    Ein Lauf, der gerade erst gestartet ist (jünger als `laeuft_hoechstens_minuten`), zählt noch nicht.
    """
    laeufe: dict[int, dict] = {}
    modus = investiert = None
    for e in eintraege:
        nummer = e.get("lauf")
        if not isinstance(nummer, int):
            continue
        lauf = laeufe.setdefault(nummer, {})
        ereignis = e.get("ereignis")
        if ereignis == "start":
            lauf["start"] = zeit_lesen(e.get("zeit"), zone_wenn_fehlt)
            lauf["zeit_roh"] = e.get("zeit")
            modus = e.get("modus", modus)
        elif ereignis == "kasse":
            lauf["kasse"] = True
            investiert = e.get("investiert", investiert)
        elif ereignis == "ende":
            lauf["ende"] = True
            lauf["ergebnis"] = e.get("ergebnis") or ""

    fertige = []
    for nummer in sorted(laeufe):
        lauf = laeufe[nummer]
        if "zeit_roh" not in lauf:
            continue  # nur kasse/ende ohne start – kaputtes Protokoll, nicht raten
        start = lauf.get("start")
        if not lauf.get("ende"):
            if start is not None and jetzt - start < laeuft_hoechstens_minuten * 60:
                continue  # läuft gerade noch
            fertige.append({"ts": start, "ok": False, "errors": ["Lauf ohne Ende (abgestürzt oder abgebrochen)"],
                            "lauf": nummer})
            continue
        ok = bool(lauf.get("kasse"))
        fertige.append({"ts": start, "ok": ok, "errors": [] if ok else [lauf["ergebnis"] or "ohne Grund"],
                        "lauf": nummer})

    stueck: dict[str, float] = {}
    for a in ausfuehrungen:
        menge = a.get("menge")
        if isinstance(menge, (int, float)) and not isinstance(menge, bool):
            vorzeichen = 1 if a.get("seite") == "KAUF" else -1
            stueck[a.get("papier")] = stueck.get(a.get("papier"), 0.0) + vorzeichen * menge
    positionen = sum(1 for s in stueck.values() if s > 1e-9)
    return {"modus": modus, "investiert": investiert, "positionen": positionen}, fertige
