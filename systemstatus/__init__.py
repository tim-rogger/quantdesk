"""Systemstatus-Sammler für die Seite /system im Dashboard.

Läuft auf dem Server direkt auf dem Host (systemd-Dienst, siehe deploy/systemstatus.service), NICHT im
Dashboard-Container. Er schaut alle 30 Sekunden nach, ob alles läuft, und schreibt das Ergebnis nach
deploy/state/system/system.json. Das Dashboard liest nur diese Datei – es kennt weder Docker noch den Host.

Warum so umständlich? Der einfache Weg wäre, dem Dashboard-Container den Docker-Socket zu geben. Wer den
Socket hat, ist auf dem Server praktisch root – und das Dashboard ist das einzige Ding, das man von aussen
(über Tailscale) erreicht, geschützt nur durch eine PIN.

Aufbau:
    messen.py    holt Rohdaten (Docker, ufw, Tailscale, Dateien der Bots, …) – hier passiert alles mit Ein-/Ausgabe
    tws.py       fragt das IB Gateway nach den Kontonummern (nur lesen, eigene Client-ID)
    bewerten.py  macht aus den Rohdaten Kästen mit grün/gelb/rot/grau – reine Regeln, gut testbar
    __main__.py  die Schleife: messen → bewerten → Datei schreiben → 30 s warten

Nur Python-Standardbibliothek: läuft mit dem python3 von Ubuntu, ohne pip install.
"""
