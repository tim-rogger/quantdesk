#!/usr/bin/env bash
# Wiederherstellung der QuantDesk-Laufzeitdaten aus dem restic-Backup (Backblaze B2).
#
#   ./restore.sh                  # neuester Stand nach ./restore-<datum>/ (zum Prüfen, überschreibt nichts)
#   ./restore.sh latest --apply   # neuester Stand nach ./state und ./ntfy-data (Dienste vorher stoppen!)
#   ./restore.sh <snapshot-id>    # bestimmter Stand (IDs: docker compose run --rm bot python -m quantdesk.backup snapshots)
#
# Quartalsweiser Restore-Test: ohne --apply ausführen und `./restore.sh check` (siehe SERVER.md, "Backups").
set -euo pipefail
cd "$(dirname "$0")"

SNAPSHOT="${1:-latest}"
APPLY="${2:-}"

if [[ "$SNAPSHOT" == "check" ]]; then
  docker compose run --rm --entrypoint "" bot python -m quantdesk.backup check
  exit $?
fi

TARGET="restore-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$TARGET"
echo "Stelle Snapshot '$SNAPSHOT' nach $TARGET wieder her ..."
docker compose run --rm --entrypoint "" -v "$PWD/$TARGET:/restore" bot \
  sh -c 'export B2_ACCOUNT_KEY="$(cat "$B2_ACCOUNT_KEY_FILE")"; restic restore "$0" --target /restore' "$SNAPSHOT"

echo
echo "Inhalt:"
find "$TARGET" -maxdepth 3 -type f | head -n 30
echo
python3 - "$TARGET" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
eq = next(root.rglob("equities.json"), None)
jr = next(root.rglob("journal.jsonl"), None)
if eq:
    systems = json.loads(eq.read_text())["systems"]
    print(f"equities.json: {len(systems)} Systeme, {sum(s['status'] == 'On' for s in systems)} eingeschaltet")
if jr:
    lines = [json.loads(l) for l in jr.read_text().splitlines() if l.strip()]
    print(f"journal.jsonl: {len(lines)} Einträge, letzter am {lines[-1]['day'] if lines else '-'}")
if not (eq and jr):
    print("WARNUNG: equities.json oder journal.jsonl fehlt im Backup!")
    sys.exit(1)
PY

if [[ "$APPLY" == "--apply" ]]; then
  read -r -p "Laufende Daten in ./state und ./ntfy-data durch dieses Backup ERSETZEN? (ja/nein) " answer
  [[ "$answer" == "ja" ]] || { echo "Abgebrochen."; exit 1; }
  docker compose stop bot dashboard ntfy
  ts="$(date +%Y%m%d-%H%M%S)"
  [[ -d state ]] && mv state "state.vor-restore-$ts"
  [[ -d ntfy-data ]] && mv ntfy-data "ntfy-data.vor-restore-$ts"
  cp -a "$TARGET/state" state
  [[ -d "$TARGET/backup/ntfy" ]] && cp -a "$TARGET/backup/ntfy" ntfy-data
  mkdir -p state/tws_settings state/cache
  sudo chown -R 1000:1000 state
  docker compose up -d
  echo "Wiederhergestellt. Der nächste Lauf gleicht Positionen und Fills mit IBKR ab."
else
  echo "Nur zur Prüfung wiederhergestellt (nichts überschrieben). Mit '--apply' übernehmen."
fi
