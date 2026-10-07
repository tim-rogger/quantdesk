# QuantDesk auf dem Server – Anleitung für Tim (Windows-Laptop)

Ziel: Der Bot läuft rund um die Uhr auf einem kleinen Server in der Schweiz, ganz ohne Laptop. Du bekommst
Push-Nachrichten aufs Handy und hast eine eigene App (Dashboard), beides nur in deinem privaten Tailscale-Netz.
Weiterhin **nur Paper-Trading**.

```
Handy / Laptop ──(Tailscale, privat)──> Server: Dashboard :443  ntfy :8443
                                        ├─ bot (Tageslauf 10:00 + 15:30 New York)
                                        └─ ib-gateway (Paper-Login, kein Port nach aussen)
Internet ──> nur SSH (Port 22, nur mit Schlüssel)
```

Dauer: etwa 1–2 Stunden. Alle Befehle mit `$` laufen **auf dem Server** (per SSH), die mit `PS>` in der
**PowerShell auf deinem Laptop**.

---

## 1. Server bestellen (machst du selbst)

- Infomaniak → **VPS Lite**, Plan mit **2 vCPU / 4 GB RAM** (IB Gateway ist Java, 2 GB reichen nicht).
- Betriebssystem **Ubuntu 24.04 LTS**, Rechenzentrum **Schweiz**.
- Bei der Bestellung fragt Infomaniak nach einem **SSH-Schlüssel** → zuerst Schritt 2 machen und den
  öffentlichen Schlüssel dort einfügen.
- Notiere dir die **IP-Adresse** des Servers und den Standard-Benutzer (bei Infomaniak meist `ubuntu`).

## 2. SSH-Schlüssel auf dem Laptop erstellen

```powershell
PS> ssh-keygen -t ed25519 -C "tim-laptop"
```
Speicherort mit Enter bestätigen, eine **Passphrase** setzen. Öffentlichen Schlüssel anzeigen und bei
Infomaniak einfügen:
```powershell
PS> Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```
Verbinden (IP ersetzen):
```powershell
PS> ssh ubuntu@203.0.113.10
```

## 3. Server absichern

```bash
$ sudo apt update && sudo apt full-upgrade -y
$ sudo apt install -y ufw unattended-upgrades fail2ban git
$ sudo dpkg-reconfigure -plow unattended-upgrades      # "Ja" wählen: Sicherheitsupdates automatisch
```
Eigener Benutzer `tim` (mit deinem Schlüssel):
```bash
$ sudo adduser tim
$ sudo usermod -aG sudo tim
$ sudo mkdir -p /home/tim/.ssh && sudo cp ~/.ssh/authorized_keys /home/tim/.ssh/
$ sudo chown -R tim:tim /home/tim/.ssh && sudo chmod 700 /home/tim/.ssh && sudo chmod 600 /home/tim/.ssh/authorized_keys
```
**Neues PowerShell-Fenster** öffnen und testen, dass `ssh tim@203.0.113.10` klappt, **bevor** du weitermachst.
Dann SSH nur noch mit Schlüssel und ohne root:
```bash
$ sudo tee /etc/ssh/sshd_config.d/99-quantdesk.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
$ sudo systemctl restart ssh
```
Firewall: nur SSH von aussen.
```bash
$ sudo ufw default deny incoming
$ sudo ufw default allow outgoing
$ sudo ufw allow OpenSSH
$ sudo ufw enable
```

## 4. Docker und Tailscale installieren

Docker (offizielles Skript):
```bash
$ curl -fsSL https://get.docker.com | sudo sh
$ sudo usermod -aG docker tim     # danach einmal ab- und wieder anmelden
```
Tailscale:
```bash
$ curl -fsSL https://tailscale.com/install.sh | sh
$ sudo tailscale up --hostname quantdesk
$ sudo ufw allow in on tailscale0  # Verkehr aus deinem privaten Tailscale-Netz erlauben
```
Der Befehl zeigt einen Link zum Anmelden → im Browser öffnen, mit deinem Tailscale-Konto bestätigen.
Tailscale auch auf **Laptop** (tailscale.com/download) und **Handy** (App Store / Play Store) installieren
und mit demselben Konto anmelden. In der Tailscale-Verwaltung unter **DNS** „MagicDNS“ und
„HTTPS Certificates“ einschalten. Der Server heisst dann z.B. `quantdesk.dein-tailnet.ts.net`.

> Docker-Ports sind in `docker-compose.yml` nur an `127.0.0.1` gebunden. Docker umgeht ufw sonst teilweise –
> deshalb nie `0.0.0.0:...` eintragen.

## 5. Repo klonen und konfigurieren

```bash
$ git clone https://github.com/tim-rogger/quantdesk.git ~/quantdesk
$ cd ~/quantdesk/deploy
$ cp .env.example .env
$ nano .env
```
Ausfüllen: `TWS_USERID` (dein **Paper**-Benutzername), `IBKR_ACCOUNT_ID` (`DUO844164`), `DASHBOARD_PIN`
(mind. 6 Ziffern), `NTFY_PUBLIC_URL=https://quantdesk.dein-tailnet.ts.net:8443`. `QUANTDESK_MODE=DRY_RUN` lassen.

Paper-Passwort als Secret (nur das Passwort, eine Zeile):
```bash
$ mkdir -p secrets && nano secrets/tws_password.txt && chmod 600 secrets/tws_password.txt
```

## 6. Laufzeitdaten vom Laptop übernehmen

**Zuerst den Bot auf dem Laptop beenden** (Fenster schliessen) und das lokale Client-Portal-Gateway stoppen.
Dann vom Laptop kopieren:
```powershell
PS> cd C:\Users\tim07\dev\workspace\03_finance\quantdesk
PS> ssh tim@quantdesk "mkdir -p ~/quantdesk/deploy/state/data"
PS> scp equities.json journal.jsonl executions.jsonl bot_orders.jsonl quantdesk.log tim@quantdesk:~/quantdesk/deploy/state/
```
(`tim@quantdesk` funktioniert über Tailscale; sonst die IP verwenden.)

## 7. Starten und ersten Lauf testen

```bash
$ cd ~/quantdesk/deploy
$ sudo chown -R 1000:1000 state     # der Bot läuft im Container als Benutzer 1000
$ docker compose up -d --build
$ docker compose logs -f ib-gateway  # warten bis "Login has completed" (1–2 Minuten), dann Strg+C
```
Falls IBKR beim Login eine **2FA-Bestätigung** will: Sie kommt auf dein Handy in **IBKR Mobile** → bestätigen.
IB Gateway verlangt etwa einmal pro Woche einen vollständigen Neu-Login. Den macht IBC automatisch, eventuell
wieder mit Bestätigung in IBKR Mobile.

Ersten Lauf von Hand starten (DRY_RUN, sendet nichts an IBKR):
```bash
$ docker compose exec bot python run_daily.py trade --force --no-push
$ docker compose logs --tail 50 bot
```
Im Log sollten stehen: Verbindung ok, Konto `DUO844164`, nachgebuchte Fills, Trend je Symbol, „DRY_RUN … NICHT gesendet“.

Wenn alles stimmt: in `.env` `QUANTDESK_MODE=PAPER` setzen und neu starten:
```bash
$ docker compose up -d
```
Ab jetzt läuft der Bot jeden NYSE-Handelstag um 10:00 und 15:30 New York (16:00 / 21:30 Schweizer Zeit).

## 8. Handy: Push und App

**Tailscale-HTTPS für Dashboard und ntfy einschalten:**
```bash
$ sudo tailscale serve --bg --https=443  http://127.0.0.1:8080   # Dashboard
$ sudo tailscale serve --bg --https=8443 http://127.0.0.1:8090   # ntfy
$ sudo tailscale serve status
```
**ntfy-Benutzer und Token anlegen:**
```bash
$ docker compose exec ntfy ntfy user add --role=admin tim
$ docker compose exec ntfy ntfy token add tim        # Token (tk_...) kopieren
```
Token in `deploy/.env` bei `NTFY_TOKEN=` eintragen, dann `docker compose up -d`. Testnachricht:
```bash
$ docker compose exec bot python -c "from quantdesk.config import load_settings; from quantdesk.notify import Notifier; print(Notifier.from_settings(load_settings()).send('QuantDesk', 'Test ok'))"
```
**ntfy-App** (Play Store / App Store) → Einstellungen → Standard-Server `https://quantdesk.dein-tailnet.ts.net:8443`,
Benutzer `tim` + Passwort → Thema `quantdesk` abonnieren.

**Dashboard:** Im Handy-Browser `https://quantdesk.dein-tailnet.ts.net` öffnen → Menü → **„Zum Home-Bildschirm“**.
Jetzt hast du die QuantDesk-App mit Kapitalkurve, Positionen, F1–F5 und STOP-ALL (PIN aus `.env`).

## 9. Laptop-Betrieb beenden

- Auf dem Laptop **nicht mehr** `start.bat` / Client-Portal-Gateway starten und nicht gleichzeitig mit dem
  Paper-Benutzer in TWS / IB Gateway einloggen. Sonst wirft IBKR eine Sitzung raus („competing session“).
- Die IBKR-App auf dem Handy zum Anschauen ist ok, kann aber gelegentlich die Gateway-Sitzung trennen. IBC loggt
  dann neu ein, im schlimmsten Fall fällt ein Tageslauf aus (du bekommst einen Push).

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
| Backup | `tar czf ~/quantdesk-backup-$(date +%F).tgz state` und per `scp` auf den Laptop holen |

**Wenn etwas nicht geht:**
- *„IB Gateway nicht erreichbar“* → `docker compose logs ib-gateway`: Login fehlgeschlagen? Passwort in
  `secrets/tws_password.txt`, 2FA in IBKR Mobile bestätigt?
- *„Konto abgelehnt: nur Paper-Konten“* → In `.env` steht kein `DU…`-Konto oder der Live-Benutzer. Richtig ist der Paper-Login.
- *Kein Push* → `NTFY_TOKEN` gesetzt? `docker compose logs ntfy`.
- *Dashboard leer* → Es hat noch kein Lauf stattgefunden (`status.json` fehlt). Lauf von Hand starten (siehe oben).
