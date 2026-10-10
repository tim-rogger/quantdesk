#!/usr/bin/env bash
# Bot C einmal im DRY_RUN mit einem anderen Code-Stand laufen lassen und die Kurse mit dem letzten echten Lauf
# vergleichen. Ausserdem: Ist stooq.com vom Server aus erreichbar?
#
# Aufruf im Ordner deploy/:   ./dry_run_kursvergleich.sh [branch]      (Standard: feature/lotse-kurse-yahoo)
#
# Sicher für den laufenden Betrieb:
#  - eigenes Image quantdesk:test aus einem eigenen Arbeitsordner – das laufende quantdesk:latest bleibt unverändert
#  - Bot C läuft auf einer KOPIE von state/ (state-dry/) – die echten Zustandsdateien werden nicht angefasst
#  - DRY_RUN + "reconcile": keine Order; eigene TWS-Client-ID 18 (der laufende Bot hat 17); kein Push, kein Healthcheck
set -euo pipefail
cd "$(dirname "$0")"

BRANCH="${1:-feature/lotse-kurse-yahoo}"
ARBEIT="$(mktemp -d)"
NETZ="quantdesk_default"

echo "== 1/4 Code-Stand $BRANCH holen und als quantdesk:test bauen"
git fetch -q origin "$BRANCH"
git worktree add -q --detach "$ARBEIT/code" "origin/$BRANCH"
docker build -q -t quantdesk:test -f "$ARBEIT/code/deploy/Dockerfile" "$ARBEIT/code" >/dev/null

echo "== 2/4 Kopie des Zustands (state/ -> state-dry/)"
rm -rf state-dry
cp -a state state-dry
rm -f state-dry/data/STOP

echo "== 3/4 Bot C im DRY_RUN auf der Kopie (reconcile, keine Order)"
docker run --rm --network "$NETZ" --env-file .env --user "$(id -u):$(id -g)" \
  -e QUANTDESK_MODE=DRY_RUN -e QUANTDESK_BROKER=tws -e TWS_HOST=ib-gateway -e TWS_PORT=4004 -e TWS_CLIENT_ID=18 \
  -e QUANTDESK_DATA_FILE=/state/equities.json -e QUANTDESK_JOURNAL_FILE=/state/journal.jsonl \
  -e QUANTDESK_EXECUTIONS_FILE=/state/executions.jsonl -e QUANTDESK_REGISTRY_FILE=/state/bot_orders.jsonl \
  -e QUANTDESK_DATA_DIR=/state/data -e QUANTDESK_LOG_FILE=/state/quantdesk.log -e QUANTDESK_CACHE_DIR=/state/cache \
  -e HEALTHCHECKS_URL= -e NTFY_URL= -e HOME=/tmp \
  -v "$PWD/state-dry:/state" quantdesk:test python run_daily.py reconcile --force --no-push || true

echo "== 4/4 Vergleich der Kurse und Stooq-Test"
docker run --rm --network "$NETZ" -v "$PWD:/deploy:ro" quantdesk:test python - <<'PY'
import json, time
import requests

def kurse(pfad):
    with open(pfad, encoding="utf-8") as f:
        status = json.load(f)
    return status.get("generated_at"), {s["symbol"]: (s.get("price"), s.get("price_source")) for s in status["systems"]}

alt_zeit, alt = kurse("/deploy/state/data/status.json")
neu_zeit, neu = kurse("/deploy/state-dry/data/status.json")
print(f"letzter echter Lauf: {alt_zeit} | DRY_RUN: {neu_zeit}")
abweichend = 0
for sym in sorted(set(alt) | set(neu)):
    (pa, qa), (pn, qn) = alt.get(sym, (None, None)), neu.get(sym, (None, None))
    diff = abs(pn / pa - 1) if pa and pn else None
    if qa != qn or diff is None or diff > 0.03:
        abweichend += 1
        print(f"  {sym:6} echt {pa} ({qa})  ->  DRY {pn} ({qn})" + (f"  Δ {diff:.1%}" if diff is not None else ""))
print(f"{len(neu)} Papiere, {abweichend} mit anderer Quelle, fehlendem Kurs oder > 3 % Abstand "
      "(Kurse bewegen sich zwischen den Läufen – entscheidend sind Quelle und Vollständigkeit)")

t = time.time()
try:
    r = requests.get("https://stooq.com/q/d/l/?s=aapl.us&i=d", timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    print(f"Stooq: HTTP {r.status_code} in {time.time() - t:.1f} s, Antwort beginnt mit {r.text[:60]!r}")
except requests.RequestException as e:
    print(f"Stooq: NICHT erreichbar nach {time.time() - t:.1f} s – {type(e).__name__}: {e}")
PY

git worktree remove --force "$ARBEIT/code"
rm -rf "$ARBEIT" state-dry
echo "Fertig. state/ und der laufende Bot wurden nicht verändert."
