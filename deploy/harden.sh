#!/usr/bin/env bash
# Härtung des QuantDesk-Servers (Ubuntu 24.04). Idempotent: darf mehrfach laufen.
#
#   sudo ./harden.sh            # alles prüfen/einrichten; die Firewall erst, wenn Tailscale verbunden ist
#   sudo ./harden.sh --dry-run  # nur anzeigen, was passieren würde
#
# Schutz vor Aussperren: Die Firewall (nur tailscale0 + 41641/udp, KEIN Port 22) wird nur aktiviert, wenn
#   1) Tailscale verbunden ist und
#   2) du NICHT über eine öffentliche SSH-Verbindung eingeloggt bist (sondern über Tailscale oder die Web-Konsole).
# Sonst bricht das Skript vor der Firewall ab und sagt, was zu tun ist.
set -euo pipefail

USER_NAME="${QUANTDESK_USER:-tim}"
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
run()  { if (( DRY )); then echo "   [dry-run] $*"; else eval "$@"; fi; }
fail() { printf '\n\033[31mABBRUCH: %s\033[0m\n' "$*"; exit 1; }

[[ $EUID -eq 0 ]] || fail "Bitte mit sudo ausführen: sudo ./harden.sh"
grep -q 'VERSION_ID="24.04"' /etc/os-release || echo "Hinweis: getestet für Ubuntu 24.04."

say "1/7 Updates und Pakete"
run "apt-get update -q"
run "DEBIAN_FRONTEND=noninteractive apt-get full-upgrade -y -q"
run "DEBIAN_FRONTEND=noninteractive apt-get install -y -q ufw fail2ban unattended-upgrades curl"

say "2/7 Zeit: UTC + Zeitsynchronisation"
run "timedatectl set-timezone UTC"
run "timedatectl set-ntp true"

say "3/7 Benutzer $USER_NAME"
if ! id "$USER_NAME" >/dev/null 2>&1; then
  run "adduser --disabled-password --gecos '' $USER_NAME"
fi
run "usermod -aG sudo $USER_NAME"
if [[ ! -s /home/$USER_NAME/.ssh/authorized_keys ]]; then
  [[ -s /root/.ssh/authorized_keys ]] || fail "/root/.ssh/authorized_keys fehlt – SSH-Schlüssel zuerst bei Contabo hinterlegen."
  run "install -d -m 700 -o $USER_NAME -g $USER_NAME /home/$USER_NAME/.ssh"
  run "install -m 600 -o $USER_NAME -g $USER_NAME /root/.ssh/authorized_keys /home/$USER_NAME/.ssh/authorized_keys"
fi
if ! passwd -S "$USER_NAME" | grep -q ' P '; then
  echo "   Hinweis: $USER_NAME hat noch kein Passwort (für sudo nötig): sudo passwd $USER_NAME"
fi

say "4/7 SSH absichern"
CONF=/etc/ssh/sshd_config.d/hardening.conf
WANT="PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers $USER_NAME
MaxAuthTries 3
X11Forwarding no"
if [[ "$(cat "$CONF" 2>/dev/null || true)" != "$WANT" ]]; then
  if (( DRY )); then echo "   [dry-run] schreibe $CONF"; else printf '%s\n' "$WANT" > "$CONF"; fi
fi
run "sshd -t"   # Konfiguration prüfen, bevor neu geladen wird
run "systemctl reload ssh"

say "5/7 Sicherheitsupdates automatisch (Neustart bei Bedarf um 03:00 UTC)"
if (( ! DRY )); then
  cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
  cat > /etc/apt/apt.conf.d/52quantdesk-reboot <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "03:00";
EOF
fi
run "systemctl enable --now unattended-upgrades"
run "systemctl enable --now fail2ban"

say "6/7 Tailscale"
if ! command -v tailscale >/dev/null; then
  run "curl -fsSL https://tailscale.com/install.sh | sh"
fi
STATE="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null || true)"
if [[ "$STATE" != "Running" ]]; then
  fail "Tailscale ist nicht verbunden. Jetzt ausführen:
     sudo tailscale up --ssh=false --hostname quantdesk
  Den Login-Link im eigenen Browser bestätigen, dann vom Laptop (im Tailnet) in einem NEUEN Fenster
  'ssh $USER_NAME@quantdesk' testen und dieses Skript erneut starten. Die Firewall wurde NICHT verändert."
fi
echo "   Tailscale verbunden: $(tailscale ip -4 2>/dev/null | head -n1)"

say "7/7 Firewall: eingehend nur Tailscale"
CLIENT_IP="${SSH_CONNECTION%% *}"
if [[ -n "${SSH_CONNECTION:-}" && ! "$CLIENT_IP" =~ ^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\. && "$CLIENT_IP" != fd7a:115c:a1e0:* ]]; then
  fail "Du bist über eine ÖFFENTLICHE SSH-Verbindung ($CLIENT_IP) eingeloggt. Nach der Firewall wäre sie weg.
  Verbinde dich über Tailscale ('ssh $USER_NAME@quantdesk') oder nutze die Contabo-Web-Konsole und starte das Skript neu."
fi
run "ufw default deny incoming"
run "ufw default allow outgoing"
run "ufw allow in on tailscale0"
run "ufw allow 41641/udp comment 'Tailscale direkt'"
for rule in OpenSSH 22/tcp 22; do   # eine frühere SSH-Freigabe aus der Ersteinrichtung entfernen
  run "ufw delete allow $rule >/dev/null 2>&1 || true"
done
run "ufw --force enable"
run "ufw status verbose"

say "Optional: Push bei jedem SSH-Login (wer, von wo)"
NTFY_ENV=/etc/quantdesk/ntfy.env
if [[ -s "$NTFY_ENV" ]]; then
  if (( ! DRY )); then
    cat > /usr/local/bin/quantdesk-ssh-notify <<'EOF'
#!/bin/sh
# von pam_exec bei jeder SSH-Sitzung aufgerufen
[ "$PAM_TYPE" = "open_session" ] || exit 0
. /etc/quantdesk/ntfy.env
curl -fsS -m 5 -H "Authorization: Bearer $NTFY_TOKEN" -H "Title: SSH-Login auf quantdesk" \
  -d "$PAM_USER von ${PAM_RHOST:-?} um $(date -u +%H:%M) UTC" "$NTFY_LOCAL_URL/$NTFY_TOPIC" >/dev/null 2>&1 || true
exit 0
EOF
    chmod 755 /usr/local/bin/quantdesk-ssh-notify
    grep -q quantdesk-ssh-notify /etc/pam.d/sshd || \
      echo "session optional pam_exec.so /usr/local/bin/quantdesk-ssh-notify" >> /etc/pam.d/sshd
  fi
  echo "   aktiv (Konfiguration: $NTFY_ENV)"
else
  echo "   übersprungen – später einrichten (SERVER.md, Schritt 8): $NTFY_ENV mit
     NTFY_LOCAL_URL=http://127.0.0.1:8090
     NTFY_TOPIC=quantdesk
     NTFY_TOKEN=tk_...
   anlegen (chmod 600) und das Skript erneut ausführen."
fi

cat <<EOF

Fertig. Prüfen:
  - Vom Laptop OHNE Tailscale: 'ssh $USER_NAME@<öffentliche IP>' darf NICHT antworten.
  - Mit Tailscale: 'ssh $USER_NAME@quantdesk' funktioniert.
  - Contabo-Panel -> Firewall: eingehend nur 41641/udp (falls genutzt). Notfall-Zugang: Contabo-Web-Konsole.
EOF
