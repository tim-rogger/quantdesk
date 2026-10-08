#!/usr/bin/env bash
# Ordner, Rechte und Secret-Dateien für QuantDesk vorbereiten. Beliebig oft ausführbar (idempotent).
# Aufruf im Ordner deploy/:   sudo ./prepare.sh
#
#  - state/ und ntfy-data/ gehören DIR (deine UID, z.B. 1001 auf Contabo) -> scp vom Laptop funktioniert
#  - Bot und Dashboard laufen mit deiner UID (QUANTDESK_UID/GID werden in .env eingetragen)
#  - IB Gateway läuft im Image als UID 1000 (ibgateway): state/tws_settings und secrets/tws_password.txt gehören 1000
#  - fehlende Secret-Dateien werden LEER angelegt (sonst startet docker compose gar nicht) – mit Warnung
#  - Secret-Dateien: Modus 400 (nur der Besitzer darf lesen)
set -euo pipefail

cd "$(dirname "$0")"
GATEWAY_UID=1000   # Benutzer "ibgateway" im Image ghcr.io/gnzsnz/ib-gateway

if [[ $EUID -ne 0 ]]; then
  echo "Bitte mit sudo ausführen: sudo ./prepare.sh" >&2
  exit 1
fi
OWNER_UID="${SUDO_UID:-$(id -u)}"
OWNER_GID="${SUDO_GID:-$(id -g)}"
if [[ "$OWNER_UID" == 0 ]]; then
  echo "Bitte als dein normaler Benutzer mit sudo starten (nicht direkt als root)." >&2
  exit 1
fi
echo "Dein Benutzer: UID $OWNER_UID, GID $OWNER_GID"

# --- .env: UID/GID für Bot und Dashboard
if [[ ! -f .env ]]; then
  echo "FEHLT: deploy/.env – zuerst 'cp .env.example .env' und ausfüllen (SERVER.md, Schritt 7)." >&2
  exit 1
fi
for kv in "QUANTDESK_UID=$OWNER_UID" "QUANTDESK_GID=$OWNER_GID"; do
  key="${kv%%=*}"
  if grep -q "^${key}=" .env; then
    sed -i "s/^${key}=.*/${kv}/" .env
  else
    echo "$kv" >> .env
  fi
done
chown "$OWNER_UID:$OWNER_GID" .env
chmod 600 .env

# --- Ordner
mkdir -p state/data state/cache state/tws_settings ntfy-data secrets
chown -R "$OWNER_UID:$OWNER_GID" state ntfy-data secrets
chown -R "$GATEWAY_UID:$GATEWAY_UID" state/tws_settings
chmod 700 secrets

# --- Secrets
warn=0
for name in tws_password restic_password b2_account_key; do
  f="secrets/${name}.txt"
  if [[ ! -s "$f" ]]; then
    touch "$f"
    echo "WARNUNG: $f ist leer – bitte füllen (nano $f), sonst funktioniert $( [[ $name == tws_password ]] && echo 'der IBKR-Login' || echo 'das Backup' ) nicht." >&2
    warn=1
  fi
done
chown "$GATEWAY_UID:$GATEWAY_UID" secrets/tws_password.txt
chown "$OWNER_UID:$OWNER_GID" secrets/restic_password.txt secrets/b2_account_key.txt
chmod 400 secrets/*.txt
# Ordner secrets/: du (Besitzer) darfst hinein; der Gateway-Container bekommt die Datei per Bind-Mount
# direkt, braucht also keinen Zugriff auf den Ordner.

echo
ls -ln secrets state | sed 's/^/  /'
if [[ $warn == 1 ]]; then
  echo
  echo "Fertig, aber mit Warnungen (leere Secret-Dateien). Zum Bearbeiten: sudo nano secrets/<datei>.txt, danach"
  echo "nochmals 'sudo ./prepare.sh' (setzt die Rechte wieder)."
else
  echo
  echo "Fertig. Weiter mit: docker compose up -d --build"
fi
