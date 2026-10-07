"""Verschlüsselte Backups der Laufzeitdaten mit restic nach Backblaze B2.

    python -m quantdesk.backup init        # einmalig: Repository anlegen
    python -m quantdesk.backup run         # sichern + alte Stände aufräumen (30 täglich, 12 monatlich)
    python -m quantdesk.backup snapshots   # vorhandene Stände anzeigen
    python -m quantdesk.backup check       # Repository prüfen

Gesichert werden die Laufzeitdaten (equities.json, journal.jsonl, Ausführungen, Register, data/, Log) und die
ntfy-Daten – NIE Secrets (die liegen in deploy/secrets und im Passwortmanager) und nicht die Kurs-/restic-Caches
oder die tws_settings des Gateways. Der Scheduler ruft `run` nach dem letzten Lauf jedes Handelstags auf.

Umgebung: RESTIC_REPOSITORY (z.B. b2:<bucket>:quantdesk), RESTIC_PASSWORD_FILE, B2_ACCOUNT_ID,
B2_ACCOUNT_KEY_FILE (Datei mit dem Application Key), BACKUP_PATHS (durch ":" getrennt, unter Windows ";").
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

DEFAULT_PATHS = ("/state", "/backup/ntfy")
EXCLUDES = ("/state/cache", "/state/tws_settings", "/state/data/run.lock", "*.tmp", ".tmp-*")
KEEP_DAILY = 30
KEEP_MONTHLY = 12


@dataclass(frozen=True)
class BackupResult:
    ok: bool
    step: str
    output: str


def configured(env=os.environ) -> bool:
    return bool(env.get("RESTIC_REPOSITORY"))


def restic_env(env=os.environ) -> dict:
    """Umgebung für restic: B2-Key aus der Secret-Datei lesen (steht nie in .env)."""
    out = dict(env)
    key_file = env.get("B2_ACCOUNT_KEY_FILE")
    if key_file and not env.get("B2_ACCOUNT_KEY"):
        with open(key_file, encoding="utf-8") as f:
            out["B2_ACCOUNT_KEY"] = f.read().strip()
    out.setdefault("RESTIC_CACHE_DIR", "/tmp/restic-cache")
    return out


def backup_paths(env=os.environ) -> list[str]:
    raw = env.get("BACKUP_PATHS")
    paths = raw.split(os.pathsep) if raw else list(DEFAULT_PATHS)  # Linux ":", Windows ";"
    return [p for p in paths if p and os.path.exists(p)]


def commands(paths: list[str]) -> list[tuple[str, list[str]]]:
    backup = ["restic", "backup", "--tag", "quantdesk", "--one-file-system"]
    for pattern in EXCLUDES:
        backup += ["--exclude", pattern]
    return [
        ("backup", backup + paths),
        ("forget", ["restic", "forget", "--tag", "quantdesk", "--keep-daily", str(KEEP_DAILY),
                    "--keep-monthly", str(KEEP_MONTHLY), "--prune"]),
    ]


def _run(cmd: list[str], env: dict, runner=subprocess.run, timeout: int = 1800) -> subprocess.CompletedProcess:
    return runner(cmd, env=env, capture_output=True, text=True, timeout=timeout)


def run_backup(env=os.environ, runner=subprocess.run, notifier=None) -> BackupResult:
    if not configured(env):
        return BackupResult(False, "config", "RESTIC_REPOSITORY nicht gesetzt – kein Backup.")
    try:
        renv = restic_env(env)
    except OSError as e:
        result = BackupResult(False, "config", f"B2-Key-Datei nicht lesbar: {e}")
        _alarm(notifier, result)
        return result
    paths = backup_paths(env)
    if not paths:
        result = BackupResult(False, "config", "Keine der Backup-Pfade existiert.")
        _alarm(notifier, result)
        return result
    output = []
    for step, cmd in commands(paths):
        try:
            proc = _run(cmd, renv, runner)
        except (OSError, subprocess.TimeoutExpired) as e:
            result = BackupResult(False, step, f"{type(e).__name__}: {e}")
            _alarm(notifier, result)
            return result
        output.append((proc.stdout or "")[-1500:])
        if proc.returncode != 0:
            result = BackupResult(False, step, (proc.stderr or proc.stdout or "")[-1500:])
            _alarm(notifier, result)
            return result
    return BackupResult(True, "done", "\n".join(output))


def _alarm(notifier, result: BackupResult) -> None:
    if notifier is not None:
        notifier.send("QuantDesk: Backup fehlgeschlagen", f"Schritt '{result.step}': {result.output[-500:]}", "error")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "run"
    if cmd == "run":
        from quantdesk.config import load_settings
        from quantdesk.notify import Notifier

        result = run_backup(notifier=Notifier.from_settings(load_settings()))
        print(result.output)
        return 0 if result.ok else 1
    if cmd in ("init", "snapshots", "check"):
        proc = subprocess.run(["restic", cmd], env=restic_env())
        return proc.returncode
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
