# QuantDesk auf dem Server – Anleitung für Tim (Windows-Laptop)

Ziel: Der Bot läuft rund um die Uhr auf einem gemieteten Server, ganz ohne Laptop. Du bekommst Push-Nachrichten
aufs Handy und hast eine eigene App (Dashboard). **Von aussen ist kein Login-Port offen**: SSH, Dashboard und Push
laufen nur über dein privates Tailscale-Netz. Dein Laptop muss also zuerst im Tailnet sein. Weiterhin **nur Paper-Trading**.

```
Laptop / Handy ──(Tailscale, privat)──> Server: SSH · Dashboard :443 · ntfy :8443
                                        ├─ bot (Tageslauf 10:00 + 15:30 New York, Backup danach)
                                        └─ ib-gateway (Paper-Login, kein Port nach aussen)
Internet ──> nur 41641/udp (Tailscale-Tunnel). Port 22 ist geschlossen.
Notfall  ──> Contabo-Web-Konsole (VNC im Contabo-Panel)
```

Dauer: etwa 2 Stunden. Befehle mit `$` laufen **auf dem Server**, die mit `PS>` in der **PowerShell auf dem Laptop**.

**Was du vorher brauchst:** einen Passwortmanager (z.B. Bitwarden oder 1Password). Dort landen das Root-Passwort,
das Paper-Passwort, der B2-Key, das restic-Passwort, der ntfy-Token und die Dashboard-PIN, und zwar nur dort und in
`deploy/secrets/` auf dem Server. Schalte **2FA** auf deinem Contabo-Konto und auf dem Konto ein, mit dem du dich bei
Tailscale anmeldest.

---

## 1. Server bestellen (machst du selbst)

- Contabo → **Cloud VPS 6** (6 vCPU, 12 GB RAM), Laufzeit **1 Monat**.
- Region **European Union**, Betriebssystem **Ubuntu 24.04 LTS**.
- Speicher: **100 GB NVMe** (gratis, schneller) oder 200 GB SSD.
- **Keine Extras:** kein Auto-Backup-Abo (das machen wir mit restic, siehe Abschnitt 10), kein Panel (cPanel/Plesk),
  keine zusätzliche IP.
- **SSH-Schlüssel hinterlegen** (Abschnitt 2 zuerst machen) und ein **Root-Passwort** setzen → sofort in den Passwortmanager.
- Notiere die **öffentliche IP** des Servers.

## 2. SSH-Schlüssel auf dem Laptop erstellen

```powershell
PS> ssh-keygen -t ed25519 -C "tim-laptop"
PS> Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```
Speicherort mit Enter bestätigen, eine **Passphrase** setzen. Die Zeile, die mit `ssh-ed25519` beginnt, bei der
Contabo-Bestellung einfügen. Eine verschlüsselte Kopie des privaten Schlüssels kommt in den Passwortmanager.

## 3. Tailscale auf Laptop und Handy

[tailscale.com/download](https://tailscale.com/download) auf dem Laptop, die Tailscale-App auf dem Handy, beide mit
**demselben Konto** anmelden (Google oder GitHub, mit 2FA). In der Tailscale-Verwaltung unter **DNS** „MagicDNS“
und „HTTPS Certificates“ einschalten.

## 4. Ersteinrichtung über die Contabo-Web-Konsole

So ist Port 22 **nie** öffentlich offen. Im Contabo-Panel beim Server **„VNC“ / Web-Konsole** öffnen und als
`root` mit dem Root-Passwort aus dem Passwortmanager einloggen.

**Updates:**
```bash
$ apt update && apt full-upgrade -y
```
**Eigener User `tim`** mit dem Schlüssel, den Contabo bei root hinterlegt hat:
```bash
$ adduser tim                          # Passwort setzen (für sudo) -> Passwortmanager
$ usermod -aG sudo tim
$ install -d -m 700 -o tim -g tim /home/tim/.ssh
$ install -m 600 -o tim -g tim /root/.ssh/authorized_keys /home/tim/.ssh/authorized_keys
```
**SSH absichern** (kein root, kein Passwort, nur tim, max. 3 Versuche):
```bash
$ cat > /etc/ssh/sshd_config.d/hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers tim
MaxAuthTries 3
X11Forwarding no
EOF
$ sshd -t && systemctl reload ssh
```
**Tailscale** (vor der Firewall):
```bash
$ curl -fsSL https://tailscale.com/install.sh | sh
$ tailscale up --ssh=false --hostname quantdesk
```
Den angezeigten Login-Link **im eigenen Browser** (Laptop) öffnen und bestätigen. Danach heisst der Server im Tailnet
`quantdesk` (vollständig z.B. `quantdesk.dein-tailnet.ts.net`).

> Die Web-Konsole hat oft ein US-Tastaturlayout. Zeichen wie `|`, `>` oder `'` können anders liegen. Lange Befehle
> lieber erst machen, wenn SSH über Tailscale läuft (Abschnitt 5). Ab dort geht alles per Kopieren und Einfügen.

## 5. SSH nur über Tailscale

Vom Laptop (Tailscale an!) in einem **neuen** PowerShell-Fenster testen:
```powershell
PS> ssh tim@quantdesk
```
Erst wenn das klappt, die Firewall einschalten. Eingehend ist dann nur Tailscale erlaubt, **kein** Port 22:
```bash
$ sudo ufw default deny incoming
$ sudo ufw default allow outgoing
$ sudo ufw allow in on tailscale0
$ sudo ufw allow 41641/udp
$ sudo ufw enable
```
**Nie** `ufw allow OpenSSH` oder `ufw allow 22/tcp` eingeben. Contabo-Firewall im Panel (falls genutzt): eingehend nur **41641/udp**.

**Kontrolle:** Tailscale am Laptop kurz **ausschalten**. Dann darf `ssh tim@<öffentliche IP>` nicht mehr antworten
(Timeout). Tailscale wieder einschalten, und `ssh tim@quantdesk` geht.

**Oder alles in einem Schritt mit dem Skript** (nach Abschnitt 6, wenn das Repo auf dem Server ist):
```bash
$ cd ~/quantdesk/deploy && sudo ./harden.sh --dry-run   # zuerst ansehen
$ sudo ./harden.sh
```
`harden.sh` erledigt Updates, Zeit (UTC + Synchronisation), User, SSH-Härtung, automatische Sicherheitsupdates
(Neustart bei Bedarf um 03:00 UTC), fail2ban, Tailscale und die Firewall. **Es bricht vor der Firewall ab**, wenn
Tailscale nicht verbunden ist oder du über eine öffentliche SSH-Verbindung eingeloggt bist. So sperrst du dich nicht aus.
Es darf mehrfach laufen.

**Tailscale-Regeln (empfohlen):** In der Tailscale-Verwaltung unter *Access controls* den Server mit dem Tag
`tag:server` versehen und nur deinen eigenen Geräten Zugriff geben (Beispiel in der Tailscale-Doku „ACL tags“).
Geht ein Gerät verloren: in Tailscale entfernen und seinen Schlüssel aus `/home/tim/.ssh/authorized_keys` löschen.

### Notweg, falls die Web-Konsole nicht geht

Nur für die Ersteinrichtung: per `ssh root@<öffentliche IP>` einloggen (Contabo lässt Port 22 anfangs offen) und
Abschnitt 4 dort ausführen. **Direkt nachdem** `ssh tim@quantdesk` über Tailscale klappt, Abschnitt 5 ausführen.
Damit ist Port 22 zu (auch in der Contabo-Firewall). Notfallzugang danach ist **immer** die Contabo-Web-Konsole.

## 6. Docker installieren und Repo holen

```bash
$ curl -fsSL https://get.docker.com | sudo sh
$ sudo usermod -aG docker tim          # danach ab- und wieder anmelden
```
Repo nur lesend holen, am besten mit einem **Deploy-Key** (GitHub → Repo → Settings → Deploy keys, „Allow write access“ **aus**):
```bash
$ ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N "" -C "quantdesk-server"
$ cat ~/.ssh/deploy_key.pub            # in GitHub als Deploy-Key eintragen
$ GIT_SSH_COMMAND="ssh -i ~/.ssh/deploy_key" git clone git@github.com:tim-rogger/quantdesk.git ~/quantdesk
$ cd ~/quantdesk && git config core.sshCommand "ssh -i ~/.ssh/deploy_key"
```

> Docker-Ports sind in `docker-compose.yml` nur an `127.0.0.1` gebunden. Docker umgeht ufw bei veröffentlichten Ports,
> deshalb **nie** `0.0.0.0:...` eintragen.

## 7. Konfiguration und Secrets

```bash
$ cd ~/quantdesk/deploy
$ cp .env.example .env && chmod 600 .env && nano .env
```
Ausfüllen: `TWS_USERID` (dein **Paper**-Benutzer), `IBKR_ACCOUNT_ID=DUO844164`, `DASHBOARD_PIN` (mind. 6 Ziffern),
`NTFY_PUBLIC_URL=https://quantdesk.dein-tailnet.ts.net:8443`, die Backup-Werte (Abschnitt 10) und `HEALTHCHECKS_URL`
(Abschnitt 11). `QUANTDESK_MODE=DRY_RUN` lassen.

Secrets als Dateien (je eine Zeile, nur der Wert):
```bash
$ mkdir -p secrets && chmod 700 secrets
$ nano secrets/tws_password.txt        # Paper-Passwort
$ nano secrets/restic_password.txt     # langes Zufalls-Passwort fürs Backup (aus dem Passwortmanager generieren)
$ nano secrets/b2_account_key.txt      # applicationKey von Backblaze (Abschnitt 10)
$ chmod 600 secrets/*
```
**Ohne `restic_password.txt` gibt es kein Backup – und ohne dieses Passwort lässt sich ein Backup nie wieder öffnen.**

## 8. Laufzeitdaten vom Laptop übernehmen

**Zuerst den Bot auf dem Laptop beenden** (Fenster schliessen) und das lokale Gateway stoppen. Dann vom Laptop:
```powershell
PS> cd C:\Users\tim07\dev\workspace\03_finance\quantdesk
PS> ssh tim@quantdesk "mkdir -p ~/quantdesk/deploy/state/data"
PS> scp equities.json journal.jsonl executions.jsonl bot_orders.jsonl quantdesk.log tim@quantdesk:~/quantdesk/deploy/state/
```

## 9. Starten, ersten Lauf testen, Handy einrichten

```bash
$ cd ~/quantdesk/deploy
$ mkdir -p state/tws_settings state/cache ntfy-data && sudo chown -R 1000:1000 state
$ docker compose up -d --build
$ docker compose logs -f ib-gateway    # warten bis "Login has completed", dann Strg+C
```
Falls IBKR eine **2FA-Bestätigung** will: Sie kommt in **IBKR Mobile** → bestätigen. IB Gateway verlangt etwa
einmal pro Woche einen vollständigen Neu-Login, den IBC übernimmt.

Ersten Lauf von Hand (DRY_RUN, sendet nichts an IBKR):
```bash
$ docker compose exec bot python run_daily.py trade --force --no-push
```
Stimmt alles (Konto `DUO844164`, Fills nachgebucht, „DRY_RUN … NICHT gesendet“): in `.env` `QUANTDESK_MODE=PAPER`,
dann `docker compose up -d`. Ab jetzt läuft der Bot jeden NYSE-Handelstag um 10:00 und 15:30 New York
(16:00 / 21:30 Schweizer Zeit).

**Dashboard und ntfy fürs Handy (nur im Tailnet):**
```bash
$ sudo tailscale serve --bg --https=443  http://127.0.0.1:8080   # Dashboard
$ sudo tailscale serve --bg --https=8443 http://127.0.0.1:8090   # ntfy
$ docker compose exec ntfy ntfy user add --role=admin tim
$ docker compose exec ntfy ntfy token add tim                   # Token (tk_...) -> .env NTFY_TOKEN + Passwortmanager
$ docker compose up -d
```
Push beim SSH-Login einrichten (optional):
```bash
$ sudo install -d -m 700 /etc/quantdesk
$ printf 'NTFY_LOCAL_URL=http://127.0.0.1:8090\nNTFY_TOPIC=quantdesk\nNTFY_TOKEN=tk_...\n' | sudo tee /etc/quantdesk/ntfy.env >/dev/null
$ sudo chmod 600 /etc/quantdesk/ntfy.env && sudo ./harden.sh
```
**ntfy-App** (Play Store / App Store): Standard-Server `https://quantdesk.dein-tailnet.ts.net:8443`, Benutzer `tim`,
Thema `quantdesk` abonnieren. **Dashboard:** im Handy-Browser `https://quantdesk.dein-tailnet.ts.net` öffnen →
**„Zum Home-Bildschirm“**.

**Laptop-Betrieb beenden:** Auf dem Laptop nicht mehr `start.bat` oder das Client-Portal-Gateway starten und nicht
parallel mit dem Paper-Benutzer in TWS oder IB Gateway einloggen. Sonst trennt IBKR eine Sitzung („competing session“).

## 10. Backups (restic → Backblaze B2, verschlüsselt)

Gesichert werden die Laufzeitdaten (`deploy/state/` ohne Caches und `tws_settings`) und `ntfy-data`, und zwar
**jeden Handelstag nach dem 15:30-Abgleich**. Aufbewahrt werden **30 tägliche und 12 monatliche** Stände. Secrets
werden nie gesichert, die liegen im Passwortmanager. Schlägt ein Backup fehl, kommt ein Push.

1. **Backblaze-Konto** erstellen (backblaze.com → B2 Cloud Storage), 2FA einschalten.
2. **Bucket** anlegen: Name z.B. `quantdesk-backup-tim`, *Private*, Verschlüsselung egal (restic verschlüsselt selbst).
3. **Application Key** nur für diesen Bucket (*Read and Write*). Notiere `keyID` und `applicationKey` (der wird nur einmal gezeigt).
4. In `deploy/.env`: `RESTIC_REPOSITORY=b2:quantdesk-backup-tim:quantdesk` und `B2_ACCOUNT_ID=<keyID>`;
   `applicationKey` in `secrets/b2_account_key.txt`, ein starkes Passwort in `secrets/restic_password.txt`
   (beides auch im Passwortmanager).
5. Einmalig das Repository anlegen und ein erstes Backup machen:
   ```bash
   $ docker compose up -d
   $ docker compose exec bot python -m quantdesk.backup init
   $ docker compose exec bot python -m quantdesk.backup run
   $ docker compose exec bot python -m quantdesk.backup snapshots
   ```

**Restore-Test einmal pro Quartal** (überschreibt nichts):
```bash
$ cd ~/quantdesk/deploy && ./restore.sh          # neuester Stand nach ./restore-<datum>/, mit Kurzprüfung
$ ./restore.sh check                             # Repository auf Fehler prüfen
$ rm -rf restore-*                               # Testkopie wieder löschen
```
**Ernstfall** (Daten kaputt oder neuer Server): Server nach den Abschnitten 1–9 aufsetzen (ohne Abschnitt 8), dann
```bash
$ ./restore.sh latest --apply
```
IBKR bleibt die Quelle der Wahrheit für Positionen und Ausführungen. Der nächste Lauf gleicht ab. Verloren gehen
höchstens Journal-Einträge seit dem letzten Backup, und auch die bucht der Bot aus den IBKR-Ausführungen nach,
sofern sie nicht älter als 7 Tage sind.

Vor grösseren Änderungen zusätzlich im Contabo-Panel einen **Snapshot** des ganzen Servers machen (bei Contabo inklusive).

## 11. Überwachung von aussen (Healthchecks.io)

Der Server kann nicht melden, dass er selbst tot ist. Das übernimmt Healthchecks.io (gratis):

1. Konto auf [healthchecks.io](https://healthchecks.io) erstellen, **neuen Check** anlegen.
2. Zeitplan: **Cron** `0 10 * * 1-5`, Zeitzone **America/New_York**, Grace Time **1 Stunde**.
3. Unter *Integrations* E-Mail (oder die Healthchecks-App) als Benachrichtigung einschalten.
4. Die **Ping-URL** (`https://hc-ping.com/...`) in `deploy/.env` bei `HEALTHCHECKS_URL=` eintragen, `docker compose up -d`.

Danach pingt der Bot nach jedem erfolgreichen 10:00-Lauf. Bei einem Fehler meldet er sich mit `/fail`, und an
US-Feiertagen schickt er ein „kein Handelstag“. Bleibt der Ping bis 11:00 New York aus, alarmiert Healthchecks.io.
Zusätzlich schickt der Server selbst um 11:00 New York einen Push, wenn der Handelslauf nicht geklappt hat.

---

## Alltag

| Was | Befehl (auf dem Server, in `~/quantdesk/deploy`) |
|---|---|
| Status | `docker compose ps` |
| Logs Bot / Gateway | `docker compose logs --tail 100 bot` / `... ib-gateway` |
| Lauf sofort | `docker compose exec bot python run_daily.py trade --force` |
| Monatsreport | `docker compose exec bot python forward_test.py report` |
| STOP-ALL aufheben | `rm state/data/STOP` (nach STOP-ALL im Dashboard) |
| Update | `git pull && docker compose up -d --build` |
| Backup-Stände | `docker compose exec bot python -m quantdesk.backup snapshots` |

**Wenn etwas nicht geht:**
- *„IB Gateway nicht erreichbar“* → `docker compose logs ib-gateway`: Login fehlgeschlagen? Passwort in
  `secrets/tws_password.txt` prüfen, 2FA in IBKR Mobile bestätigen.
- *„Konto abgelehnt: nur Paper-Konten“* → In `.env` steht kein `DU…`-Konto oder der Live-Benutzer. Richtig ist der Paper-Login.
- *Kein Push* → `NTFY_TOKEN` gesetzt? `docker compose logs ntfy`.
- *„Backup fehlgeschlagen“* → `docker compose exec bot python -m quantdesk.backup run` zeigt den Fehler.
  Meist ist der B2-Key falsch oder das Repository noch nicht initialisiert.
- *Ausgesperrt (kein Tailscale)* → Contabo-Web-Konsole, dort `sudo tailscale up` bzw. `sudo ufw status`.
- *Dashboard leer* → Es hat noch kein Lauf stattgefunden (`status.json` fehlt). Lauf von Hand starten (siehe oben).
