"""Setzt Broker, Marktdaten, Engine und AI anhand der Settings zusammen (ohne GUI)."""
from __future__ import annotations

import queue
from dataclasses import dataclass

from quantdesk.ai import PortfolioManager
from quantdesk.broker.base import Broker
from quantdesk.broker.dry_run import DryRunBroker, OfflineBroker
from quantdesk.broker.ibkr import IbkrClient
from quantdesk.config import Settings, validate_account_id
from quantdesk.engine import Engine
from quantdesk.executions import ExecutionArchive
from quantdesk.history import load_history
from quantdesk.journal import Journal
from quantdesk.marketdata import MarketData
from quantdesk.registry import OrderRegistry
from quantdesk.trend import TrendFilter


@dataclass
class Services:
    settings: Settings
    broker: Broker
    marketdata: MarketData
    engine: Engine
    ai: PortfolioManager
    offline: bool


def build_broker(settings: Settings) -> tuple[Broker, bool]:
    """Echter Broker nach QUANTDESK_BROKER (ohne DRY_RUN-Hülle). Zweiter Wert: offline (kein Konto)."""
    if not settings.ibkr_account_id:
        return OfflineBroker(), True
    if settings.broker == "tws":
        from quantdesk.broker.tws import TwsBroker

        return TwsBroker(settings.tws_host, settings.tws_port, settings.tws_client_id, settings.ibkr_account_id), False
    return IbkrClient(settings.ibkr_base_url, settings.ibkr_account_id), False


def build_services(settings: Settings, events: queue.Queue | None = None, infer_missing: bool = False) -> Services:
    validate_account_id(settings.ibkr_account_id, settings.mode)
    inner, offline = build_broker(settings)
    marketdata = MarketData(inner)
    if settings.dry_run:
        broker: Broker = DryRunBroker(inner, marketdata.get_price)
    else:
        if offline:  # doppelt abgesichert – validate_account_id verhindert das schon
            raise ValueError("PAPER-Modus ohne IBKR-Konto ist nicht möglich.")
        broker = inner
    engine = Engine(
        broker,
        marketdata,
        settings.data_file,
        order_qty=settings.order_qty,
        interval_seconds=settings.interval_seconds,
        events=events,
        trend=TrendFilter(),
        journal=Journal(settings.journal_file),
        executions=ExecutionArchive(settings.executions_file),
        bars=lambda symbol: load_history(symbol, 1),
        registry=OrderRegistry(settings.registry_file),
        infer_missing=infer_missing,
    )
    ai = PortfolioManager(broker, engine.systems_snapshot, settings.anthropic_model, settings.news_enabled)
    return Services(settings, broker, marketdata, engine, ai, offline)
