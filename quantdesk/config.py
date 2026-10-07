"""Zentrale Konfiguration – alles kommt aus der .env-Datei (bzw. Umgebungsvariablen)."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv ist optional (z.B. in CI)
    pass

# Deine bestehenden Verbindungen aus dem Java-QuantDesk
DEFAULT_IBKR_BASE_URL = "https://localhost:5000/v1/api"
STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
# Fallback, weil Stooq automatisierte Abrufe inzwischen per JavaScript-Check blockiert
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"

MODES = ("DRY_RUN", "PAPER")
BROKERS = ("clientportal", "tws")
PAPER_ACCOUNT_PREFIX = "DU"


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    mode: str
    ibkr_base_url: str
    ibkr_account_id: str
    anthropic_model: str
    interval_seconds: int
    order_qty: int
    data_file: str
    news_enabled: bool
    journal_file: str = "journal.jsonl"
    executions_file: str = "executions.jsonl"
    registry_file: str = "bot_orders.jsonl"
    data_dir: str = "data"  # Snapshots, Status fürs Dashboard, Lauf-Protokoll, STOP-Schalter
    broker: str = "clientportal"  # clientportal (Laptop, GUI) | tws (IB Gateway, Server)
    tws_host: str = "127.0.0.1"
    tws_port: int = 4002
    tws_client_id: int = 17
    ntfy_url: str = ""  # z.B. http://ntfy:80 – leer = keine Push-Nachrichten
    ntfy_topic: str = "quantdesk"
    ntfy_token: str = ""
    dashboard_pin: str = ""

    @property
    def dry_run(self) -> bool:
        return self.mode != "PAPER"


def validate_account_id(account_id: str, mode: str) -> None:
    """Sicherheits-Check: nur IBKR-Paper-Konten (DU...) sind erlaubt.

    Im DRY_RUN darf die Konto-ID leer sein – dann läuft der Bot offline (ohne Gateway,
    Preise von Stooq). Im PAPER-Modus ist ein DU-Konto Pflicht.
    """
    if not account_id:
        if mode == "PAPER":
            raise ValueError("PAPER-Modus braucht IBKR_ACCOUNT_ID (dein Paper-Konto, beginnt mit 'DU').")
        return
    if not account_id.upper().startswith(PAPER_ACCOUNT_PREFIX):
        raise ValueError(
            f"IBKR_ACCOUNT_ID '{account_id}' ist kein Paper-Konto. "
            f"Erlaubt sind nur Konten, die mit '{PAPER_ACCOUNT_PREFIX}' beginnen – Start verweigert."
        )


def load_settings() -> Settings:
    mode = os.getenv("QUANTDESK_MODE", "DRY_RUN").strip().upper()
    if mode not in MODES:
        raise ValueError(f"QUANTDESK_MODE muss einer von {MODES} sein, nicht '{mode}'")
    account_id = os.getenv("IBKR_ACCOUNT_ID", "").strip().upper()
    validate_account_id(account_id, mode)
    return Settings(
        mode=mode,
        ibkr_base_url=os.getenv("IBKR_BASE_URL", DEFAULT_IBKR_BASE_URL).rstrip("/"),
        ibkr_account_id=account_id,
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8").strip(),
        interval_seconds=max(5, int(os.getenv("QUANTDESK_INTERVAL_SECONDS", "15"))),
        order_qty=max(1, int(os.getenv("QUANTDESK_ORDER_QTY", "1"))),
        data_file=os.getenv("QUANTDESK_DATA_FILE", "equities.json"),
        news_enabled=_bool(os.getenv("QUANTDESK_NEWS_ENABLED"), True),
        journal_file=os.getenv("QUANTDESK_JOURNAL_FILE", "journal.jsonl"),
        executions_file=os.getenv("QUANTDESK_EXECUTIONS_FILE", "executions.jsonl"),
        registry_file=os.getenv("QUANTDESK_REGISTRY_FILE", "bot_orders.jsonl"),
        data_dir=os.getenv("QUANTDESK_DATA_DIR", "data"),
        broker=_broker(os.getenv("QUANTDESK_BROKER", "clientportal")),
        tws_host=os.getenv("TWS_HOST", "127.0.0.1").strip(),
        tws_port=int(os.getenv("TWS_PORT", "4002")),
        tws_client_id=int(os.getenv("TWS_CLIENT_ID", "17")),
        ntfy_url=os.getenv("NTFY_URL", "").strip().rstrip("/"),
        ntfy_topic=os.getenv("NTFY_TOPIC", "quantdesk").strip(),
        ntfy_token=os.getenv("NTFY_TOKEN", "").strip(),
        dashboard_pin=os.getenv("DASHBOARD_PIN", "").strip(),
    )


def _broker(value: str) -> str:
    value = value.strip().lower()
    if value not in BROKERS:
        raise ValueError(f"QUANTDESK_BROKER muss einer von {BROKERS} sein, nicht '{value}'")
    return value
