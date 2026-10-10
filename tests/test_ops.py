"""Betrieb auf dem Server: Backup (restic gemockt), Healthchecks-Ping, Wächter, Feiertage, Ressourcen-Alarm,
Ereignis-Verlauf, Sperre bei fehlenden eigenen Aktien, Compose-Härtung und Secret-Scan."""
import datetime as dt
import json
import os
import queue
import re
import subprocess
from types import SimpleNamespace as NS

import requests
import yaml

import run_daily
import scheduler
from quantdesk import backup, healthcheck
from quantdesk.broker.base import Position
from quantdesk.engine import Engine
from quantdesk.journal import Journal
from quantdesk.notify import Notifier
from quantdesk.registry import OrderRegistry
from quantdesk.status import read_jsonl
from tests.fakes import FakeBroker, FakeMarketData
from tests.test_server import env, fake_services

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


# ====================================================================== Backup
class Runner:
    def __init__(self, fail_step=None, exc=None):
        self.calls, self.envs = [], []
        self.fail_step, self.exc = fail_step, exc

    def __call__(self, cmd, env=None, capture_output=True, text=True, timeout=None):
        self.calls.append(cmd)
        self.envs.append(env)
        if self.exc:
            raise self.exc
        failed = self.fail_step and cmd[1] == self.fail_step
        return NS(returncode=1 if failed else 0, stdout="ok", stderr="b2: unauthorized" if failed else "")


def backup_env(tmp_path):
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "ntfy").mkdir()
    key = tmp_path / "b2key.txt"
    key.write_text("K005secret\n", encoding="utf-8")
    return {"RESTIC_REPOSITORY": "b2:quantdesk-backup-tim:quantdesk", "RESTIC_PASSWORD_FILE": "/run/secrets/x",
            "B2_ACCOUNT_ID": "005abc", "B2_ACCOUNT_KEY_FILE": str(key),
            "BACKUP_PATHS": os.pathsep.join(str(tmp_path / p) for p in ("state", "ntfy", "fehlt"))}


def test_backup_runs_backup_then_forget_with_retention(tmp_path):
    r = Runner()
    result = backup.run_backup(backup_env(tmp_path), runner=r)
    assert result.ok
    bk, fg = r.calls
    assert bk[:2] == ["restic", "backup"] and bk[-2:] == [str(tmp_path / "state"), str(tmp_path / "ntfy")]  # fehlender Pfad weg
    for pattern in ("/state/cache", "/state/tws_settings"):
        assert pattern in bk
    assert fg == ["restic", "forget", "--tag", "quantdesk", "--keep-daily", "30", "--keep-monthly", "12", "--prune"]
    assert r.envs[0]["B2_ACCOUNT_KEY"] == "K005secret"  # Key aus der Secret-Datei, nie aus .env


def test_backup_failure_sends_push(tmp_path):
    push = Notifier()
    result = backup.run_backup(backup_env(tmp_path), runner=Runner(fail_step="backup"), notifier=push)
    assert not result.ok and result.step == "backup"
    assert push.sent and "Backup fehlgeschlagen" in push.sent[0]["title"] and push.sent[0]["priority"] == 5
    push = Notifier()
    result = backup.run_backup(backup_env(tmp_path / "b"), runner=Runner(exc=subprocess.TimeoutExpired("restic", 1)),
                               notifier=push)
    assert not result.ok and push.sent


def test_backup_not_configured_does_nothing(tmp_path):
    r = Runner()
    assert backup.run_backup({}, runner=r).ok is False and r.calls == []


# ====================================================================== Healthchecks
class Session:
    def __init__(self, fail=False):
        self.urls, self.fail = [], fail

    def post(self, url, data=None, timeout=None):
        self.urls.append(url)
        if self.fail:
            raise requests.ConnectionError("down")
        return NS(raise_for_status=lambda: None)


def test_healthcheck_ping_urls():
    s = Session()
    assert healthcheck.ping("success", "ok", url="https://hc-ping.com/abc/", session=s)
    assert healthcheck.ping("fail", "x", url="https://hc-ping.com/abc", session=s)
    assert s.urls == ["https://hc-ping.com/abc", "https://hc-ping.com/abc/fail"]
    assert healthcheck.ping("success", url="", session=s) is False and len(s.urls) == 2
    assert healthcheck.ping("success", url="https://hc-ping.com/abc", session=Session(fail=True)) is False


def test_run_daily_pings_healthchecks(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    pings = []
    monkeypatch.setattr(run_daily, "ping", lambda status, msg="": pings.append(status))
    monkeypatch.setattr(run_daily, "load_report", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    monkeypatch.setattr(run_daily, "PASS_WAIT_SECONDS", 0)
    monkeypatch.setattr(run_daily, "build_services", fake_services(tmp_path, FakeBroker()))
    assert run_daily.run("reconcile", force=True, push=False) == 0
    assert pings == []  # nur der Handelslauf meldet sich
    assert run_daily.run("trade", force=True, push=False) == 0
    assert pings == ["success"]

    from quantdesk.broker.base import BrokerError

    class Down(FakeBroker):
        def auth_status(self):
            raise BrokerError("IB Gateway nicht erreichbar")

    monkeypatch.setattr(run_daily, "build_services", fake_services(tmp_path / "d", Down()))
    (tmp_path / "d").mkdir()
    assert run_daily.run("trade", force=True, push=False) == 1
    assert pings == ["success", "fail"]


def test_run_daily_writes_event_history(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    monkeypatch.setattr(run_daily, "ping", lambda *a, **k: None)
    monkeypatch.setattr(run_daily, "load_report", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    monkeypatch.setattr(run_daily, "PASS_WAIT_SECONDS", 0)
    monkeypatch.setattr(run_daily, "build_services", fake_services(tmp_path, FakeBroker()))
    run_daily.run("trade", force=True, push=False)
    events = read_jsonl(str(tmp_path / "data" / "events.jsonl"))
    assert any("Einstieg Market-Buy" in e["message"] for e in events)
    status = json.loads((tmp_path / "data" / "status.json").read_text(encoding="utf-8"))
    assert status["events"] and status["events"][-1]["message"] == events[-1]["message"]


def test_resource_warnings(tmp_path, monkeypatch):
    mem = tmp_path / "meminfo"
    mem.write_text("MemTotal:       12000000 kB\nMemAvailable:     600000 kB\n", encoding="utf-8")
    monkeypatch.setattr(run_daily.shutil, "disk_usage", lambda p: NS(total=100, used=90, free=10))
    warnings = run_daily.resource_warnings(str(tmp_path), str(mem))
    assert any("Speicher 90%" in w for w in warnings) and any("RAM 95%" in w for w in warnings)
    mem.write_text("MemTotal: 12000000 kB\nMemAvailable: 9000000 kB\n", encoding="utf-8")
    monkeypatch.setattr(run_daily.shutil, "disk_usage", lambda p: NS(total=100, used=40, free=60))
    assert run_daily.resource_warnings(str(tmp_path), str(mem)) == []


# ====================================================================== Wächter, Feiertage, Backup-Zeitpunkt
def test_watchdog_pushes_when_no_successful_trade_run(tmp_path):
    day = dt.date(2026, 10, 8)
    data = tmp_path / "data"
    data.mkdir()
    push = Notifier()
    assert scheduler.watchdog(str(data), day, push) is False
    assert "kein Lauf" in push.sent[0]["title"]
    (data / "runs.jsonl").write_text(json.dumps({"mode": "trade", "day": "2026-10-08", "ok": False}) + "\n", encoding="utf-8")
    assert scheduler.watchdog(str(data), day, Notifier()) is False  # fehlgeschlagener Lauf zählt nicht
    with open(data / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"mode": "trade", "day": "2026-10-08", "ok": True}) + "\n")
    push = Notifier()
    assert scheduler.watchdog(str(data), day, push) is True and push.sent == []


def test_scheduler_execute_reconcile_then_backup_and_holiday_ping(monkeypatch):
    calls = []
    runner = lambda cmd, timeout=None: calls.append(cmd[1:]) or NS(returncode=0)
    assert scheduler.execute("reconcile", dt.date(2026, 10, 8), runner=runner) == 0
    assert calls == [["run_daily.py", "reconcile"], ["-m", "quantdesk.backup", "run"]]
    calls.clear()
    scheduler.execute("trade", dt.date(2026, 10, 8), runner=runner)
    assert calls == [["run_daily.py", "trade"]]  # Backup nur nach dem letzten Lauf des Tages
    pings = []
    monkeypatch.setattr(scheduler, "ping", lambda status, msg="": pings.append((status, msg)))
    scheduler.execute("holiday", dt.date(2026, 11, 26))
    assert pings == [("success", "2026-11-26: kein NYSE-Handelstag")]


# ====================================================================== Engine: fehlende eigene Aktien sperren das Symbol
def test_missing_own_shares_stop_trading_the_symbol(tmp_path):
    b = FakeBroker()
    e = Engine(b, FakeMarketData(), str(tmp_path / "eq.json"), events=queue.Queue(), trend=lambda s, n: True,
               journal=Journal(str(tmp_path / "j.jsonl")), registry=OrderRegistry(str(tmp_path / "r.jsonl")))
    e.add_system("KO", 3, 0.02, trend_sma=200, trend_exit=True, order_usd=1000)
    e.toggle(["KO"])
    e.run_once()
    b.fill("O1", 100.0)
    b.positions["KO"] = Position("KO", 4, 100.0)  # 6 eigene Stück fehlen
    e.run_once()
    assert len(b.placed) == 1  # keine Levels platziert
    msgs = [e.events.get() for _ in range(e.events.qsize())]
    assert any(m.level == "error" and "nicht weiter gehandelt" in m.message for m in msgs)
    e.trend = lambda s, n: False
    e.run_once()
    assert len(b.placed) == 1  # auch kein Trend-Verkauf, bis es geklärt ist


# ====================================================================== Compose-Härtung
def test_compose_hardening():
    d = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
    limits = {"ib-gateway": "1536m", "bot": "512m", "dashboard": "256m", "ntfy": "128m", "lotse": "256m"}
    for name, svc in d["services"].items():
        assert "no-new-privileges:true" in svc["security_opt"], name
        assert svc["mem_limit"] == limits[name], name
        assert svc["logging"]["options"] == {"max-size": "10m", "max-file": "3"}, name
        assert svc["restart"] == "unless-stopped", name
        for port in svc.get("ports", []):
            assert port.startswith("127.0.0.1:"), (name, port)
    assert "ports" not in d["services"]["ib-gateway"]
    for name in ("dashboard", "ntfy", "bot", "lotse"):
        assert d["services"][name]["read_only"] is True and "/tmp" in d["services"][name]["tmpfs"]
    assert re.fullmatch(r"binwiederhier/ntfy:v\d+\.\d+\.\d+", d["services"]["ntfy"]["image"])
    gw = d["services"]["ib-gateway"]["environment"]
    assert gw["TRADING_MODE"] == "paper" and "BYPASS_WARNING" not in gw and "TWS_PASSWORD" not in gw
    assert set(d["secrets"]) == {"tws_password", "restic_password", "b2_account_key"}
    # beide eigenen Dienste bauen das Image selbst (nie von Docker Hub pullen) und laufen mit Tims UID
    for name in ("bot", "dashboard", "lotse"):
        svc = d["services"][name]
        assert svc["build"]["dockerfile"] == "deploy/Dockerfile" and svc["image"] == "quantdesk:latest", name
        assert svc["user"] == "${QUANTDESK_UID:-1000}:${QUANTDESK_GID:-1000}", name
        assert svc["environment"]["HOME"] == "/tmp", name


def test_prepare_script_sets_owners_and_placeholder_secrets():
    text = (ROOT / "deploy" / "prepare.sh").read_text(encoding="utf-8")
    assert "SUDO_UID" in text and "GATEWAY_UID=1000" in text
    assert 'chown "$GATEWAY_UID:$GATEWAY_UID" secrets/tws_password.txt' in text
    assert "chown -R \"$GATEWAY_UID:$GATEWAY_UID\" state/tws_settings" in text
    assert "chmod 400 secrets/*.txt" in text and "QUANTDESK_UID=" in text
    for name in ("tws_password", "restic_password", "b2_account_key"):
        assert name in text
    env = (ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")
    assert "QUANTDESK_UID=" in env and "QUANTDESK_GID=" in env
    server = (ROOT / "deploy" / "SERVER.md").read_text(encoding="utf-8")
    assert "chown -R 1000:1000 state" not in server and "prepare.sh" in server


def test_harden_script_never_opens_ssh_port():
    text = (ROOT / "deploy" / "harden.sh").read_text(encoding="utf-8")
    assert "ufw allow in on tailscale0" in text and "ufw allow 41641/udp" in text
    assert not re.search(r"ufw allow (OpenSSH|22)", text)
    assert text.index('"Running"') < text.index("ufw default deny incoming")  # erst Tailscale prüfen, dann Firewall
    assert "AllowUsers" in text and "MaxAuthTries 3" in text


# ====================================================================== Secret-Scan (läuft auch in der CI)
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\btk_[a-z0-9]{29}\b"),  # ntfy-Token
    re.compile(r"\bK00[0-9][A-Za-z0-9+/]{20,}"),  # Backblaze applicationKey
    re.compile(r"^(TWS_PASSWORD|RESTIC_PASSWORD|B2_ACCOUNT_KEY|NTFY_TOKEN|DASHBOARD_PIN|ANTHROPIC_API_KEY|"
               r"LOTSE_FLEX_TOKEN)=\S+", re.M),
]


def test_no_secrets_in_tracked_files():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    hits = []
    for name in files:
        if name.endswith((".png", ".ico")) or name == "tests/test_ops.py":
            continue
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        hits += [f"{name}: {p.pattern}" for p in SECRET_PATTERNS if p.search(text)]
    assert hits == [], hits
