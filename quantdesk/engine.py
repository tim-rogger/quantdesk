"""trade_systems()-Loop: Einstieg + Grid-Limit-Orders, läuft im Hintergrund-Thread.

Die GUI ruft Engine-Methoden nie direkt im Tk-Thread auf, sondern über Hintergrund-Threads,
und liest nur `engine.view` / `engine.status_view` (unveränderliche Kopien). Meldungen an die
GUI laufen über `engine.events` (queue.Queue) – Tkinter selbst wird nur im Haupt-Thread angefasst.
"""
from __future__ import annotations

import calendar
import copy
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from quantdesk import storage
from quantdesk.broker.base import LOGIN_HINT, Broker, BrokerError, NotTradableError, Order, Position
from quantdesk.executions import ExecutionArchive, OrderFill, aggregate
from quantdesk.history import Bar
from quantdesk.journal import Journal
from quantdesk.registry import ENTRY, EXIT, LEVEL, BotOrder, OrderRegistry
from quantdesk.marketdata import MarketData, Quote
from quantdesk.storage import StorageError
from quantdesk.strategy import (
    CANCELLED,
    FILLED,
    OFF,
    ON,
    PENDING,
    PLACED,
    EquitySystem,
    ValidationError,
)

log = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 60
ENTRY_ORDER_TIMEOUT = 300  # Einstiegs-Order weder offen noch gefüllt auffindbar -> nach 5 min neu versuchen
EXEC_REFRESH_SECONDS = 300  # Ausführungen höchstens alle 5 min neu abfragen (beim Start immer)


@dataclass(frozen=True)
class Event:
    level: str  # "info" | "warn" | "error" | "order"
    message: str
    symbol: str | None = None
    ts: float = field(default_factory=time.time)


class Engine:
    def __init__(
        self,
        broker: Broker,
        marketdata: MarketData,
        data_file: str,
        order_qty: int = 1,
        interval_seconds: float = 15,
        events: queue.Queue | None = None,
        clock: Callable[[], float] = time.time,
        trend: Callable[[str, int], bool | None] | None = None,
        journal: Journal | None = None,
        executions: ExecutionArchive | None = None,
        bars: Callable[[str], list[Bar]] | None = None,
        registry: OrderRegistry | None = None,
        infer_missing: bool = False,
    ):
        self.broker = broker
        self.marketdata = marketdata
        self.data_file = data_file
        self.order_qty = max(1, int(order_qty))
        self.interval_seconds = interval_seconds
        self.events: queue.Queue = events if events is not None else queue.Queue()
        self._clock = clock
        self.trend = trend  # (Symbol, SMA-Tage) -> True/False/None (None = unbekannt)
        self.journal = journal
        self.executions = executions  # Archiv aller IBKR-Ausführungen (IBKR selbst liefert nur 7 Tage)
        self.bars = bars  # Tageskurse, um den Tag geschätzter Fills zu bestimmen
        self.registry = registry  # alle Orders, die der Bot selbst platziert hat
        self.infer_missing = infer_missing  # nur für die einmalige Migration
        self._booked: set[str] = set()
        self._last_exec_fetch: float | None = None
        self._fills: dict[str, OrderFill] = {}
        self._lock = threading.RLock()
        self._halt = threading.Event()  # STOP ALL
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_msgs: dict[str, str] = {}
        self._last_keepalive = 0.0
        self.quotes: dict[str, Quote] = {}
        self._status = {"authenticated": None, "connected": None, "cash": None, "last_cycle": None}
        self.view: tuple[dict, ...] = ()
        self.status_view: dict = {}

        self.systems: dict[str, EquitySystem] = {}
        try:
            self.systems = storage.load(data_file)
        except StorageError as e:
            self._emit("error", str(e))
        self._drop_simulated_state()
        self._migrate_from_journal()
        if self.journal is not None:
            try:
                self._booked = self.journal.order_ids()
            except (OSError, ValueError) as e:
                self._emit("error", f"Journal nicht lesbar: {e}")
        self._publish()

    # ------------------------------------------------------------ Hilfen
    @property
    def paused(self) -> bool:
        return self._halt.is_set()

    def _emit(self, level: str, message: str, symbol: str | None = None, key: str | None = None) -> None:
        """Event an die GUI. Mit `key` wird dieselbe Meldung nicht jede Runde wiederholt."""
        if key is not None:
            if self._last_msgs.get(key) == message:
                return
            self._last_msgs[key] = message
        getattr(log, {"order": "info", "warn": "warning"}.get(level, level), log.info)(message)
        self.events.put(Event(level, message, symbol))

    def _clear(self, key: str) -> None:
        self._last_msgs.pop(key, None)

    def _save(self) -> None:
        try:
            storage.save(self.data_file, self.systems)
        except OSError as e:
            self._emit("error", f"Konnte {self.data_file} nicht speichern: {e}", key="save")

    def _publish(self) -> None:
        with self._lock:
            rows = []
            for s in self.systems.values():
                q = self.quotes.get(s.symbol)
                rows.append(
                    {
                        "symbol": s.symbol,
                        "position": s.position,
                        "entry_price": s.entry_price,
                        "status": s.status,
                        "num_levels": s.num_levels,
                        "drawdown": s.drawdown,
                        "levels": [(lv.level, lv.price, lv.status) for lv in s.levels],
                        "entry_pending": s.entry_price is None and s.entry_order_id is not None,
                        "open_orders": len(s.open_order_ids()),
                        "trend_label": s.trend_label,
                        "exit_pending": s.exit_order_id is not None,
                        "bot_qty": s.bot_qty(),
                        "not_tradable": s.not_tradable,
                        "closed": s.closed,
                        "last_price": q.price if q else None,
                        "price_source": q.source if q else None,
                    }
                )
            self.view = tuple(rows)
            self.status_view = {**self._status, "paused": self.paused}

    def _drop_simulated_state(self) -> None:
        """Simulierter DRY_RUN-Zustand überlebt keinen Neustart (sonst glaubt PAPER an Fake-Orders)."""
        changed = False
        for s in self.systems.values():
            if s.simulated:
                s.reset_trading_state()
                changed = True
                self._emit("info", f"{s.symbol}: simulierter DRY_RUN-Zustand zurückgesetzt.", s.symbol)
        if changed:
            self._save()

    def _migrate_from_journal(self) -> None:
        """Ältere Zustände ohne Stückzahlen aus dem Journal ergänzen (für den Abgleich mit der Position)."""
        if self.journal is None:
            return
        try:
            fills = self.journal.read()
        except (OSError, ValueError):
            return
        changed = False
        for s in self.systems.values():
            if s.entry_price is None or s.entry_qty is not None:
                continue
            mine = [f for f in fills if f.symbol == s.symbol]
            last_exit = max((f.ts for f in mine if f.side == "SELL"), default=-1.0)
            entries = [f for f in mine if f.kind in ("entry", "adopted") and f.ts > last_exit]
            if not entries:
                continue
            s.entry_qty, s.entry_ts = entries[-1].qty, entries[-1].ts
            by_order = {f.order_id: f for f in mine if f.kind == "level" and f.ts > last_exit}
            for lv in s.levels:
                if lv.status == FILLED and lv.qty is None and lv.order_id in by_order:
                    lv.qty = by_order[lv.order_id].qty
            changed = True
        if changed:
            self._save()

    # ------------------------------------------------- Befehle (aus der GUI)
    def add_system(
        self,
        symbol: str,
        num_levels: int,
        drawdown: float,
        trend_sma: int | None = None,
        trend_exit: bool = False,
        order_usd: float | None = None,
    ) -> None:
        with self._lock:
            if symbol in self.systems:
                raise ValidationError(f"{symbol} ist schon in der Liste.")
            s = EquitySystem(symbol, num_levels, drawdown, trend_sma=trend_sma, trend_exit=trend_exit, order_usd=order_usd)
            self.systems[symbol] = s
            self._save()
        self._publish()
        extra = f", {s.trend_label}" if s.trend_label else ""
        self._emit("info", f"{symbol} hinzugefügt: {num_levels} Levels à {drawdown:.2%}{extra} (Status Off).", symbol)

    def toggle(self, symbols: list[str]) -> None:
        with self._lock:
            for sym in symbols:
                s = self.systems.get(sym)
                if s is None:
                    continue
                s.status = OFF if s.is_on else ON
                self._emit("info", f"{sym}: System {s.status}.", sym)
                if s.is_on and self.paused:
                    self._emit("warn", "Engine ist pausiert (STOP ALL) – mit 'Resume' wieder starten.")
            self._save()
        self._publish()

    def remove(self, symbol: str, cancel_orders: bool) -> None:
        with self._lock:
            s = self.systems.pop(symbol, None)
            if s is None:
                return
            self._save()
        self._publish()
        self._emit("info", f"{symbol} entfernt.", symbol)
        if not cancel_orders:
            return
        for order_id in s.open_order_ids():
            try:
                self.broker.cancel_order(order_id)
                self._emit("order", f"{symbol}: Order {order_id} storniert.", symbol)
            except BrokerError as e:
                self._emit("error", f"{symbol}: Storno von {order_id} fehlgeschlagen: {e}", symbol)

    def open_order_count(self, symbol: str) -> int:
        with self._lock:
            s = self.systems.get(symbol)
            return len(s.open_order_ids()) if s else 0

    def stop_all(self) -> None:
        """Kill-Switch: sofort pausieren (wirkt auch mitten in einer Runde), alle Systeme Off."""
        self._halt.set()
        self._publish()
        with self._lock:
            for s in self.systems.values():
                s.status = OFF
            self._save()
        self._publish()
        self._emit("warn", "STOP ALL: alle Systeme Off, Engine pausiert. Bestehende Orders bleiben bei IBKR offen.")

    def resume(self) -> None:
        self._halt.clear()
        self._publish()
        self._emit("info", "Engine läuft wieder. Systeme einzeln mit 'Toggle' einschalten.")

    def systems_snapshot(self) -> list[dict]:
        with self._lock:
            return [copy.deepcopy(s.to_dict()) for s in self.systems.values()]

    # ------------------------------------------------------------ Thread
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._run, name="quantdesk-engine", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._shutdown.set()
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._shutdown.is_set():
            try:
                self.run_once()
            except Exception as e:  # der Bot darf nie abstürzen
                log.exception("Unerwarteter Fehler im Engine-Loop")
                self._emit("error", f"Unerwarteter Fehler: {e}", key="loop")
            self._shutdown.wait(self.interval_seconds)

    # --------------------------------------------------------- eine Runde
    def run_once(self, decide: bool = True) -> None:
        """Eine Runde. decide=False: nur abgleichen und buchen, keine neuen Orders, keine Stornos, keine Verkäufe."""
        self._keepalive()
        authed = self._check_auth()
        with self._lock:
            symbols = list(self.systems)
            active = [s.symbol for s in self.systems.values() if s.is_on and s.tradable]
        self._refresh_quotes(symbols)
        if authed:
            self._refresh_cash()
        if not authed or self.paused or not active:
            self._finish_cycle()
            return
        try:
            positions = {p.symbol: p for p in self.broker.get_positions()}
            orders = self.broker.get_orders()
        except BrokerError as e:
            # Ohne sichere Daten wird NICHTS gekauft (Bug im Original: Market-Buy bei jedem Fehler)
            self._emit("error", f"Konto nicht lesbar, Runde übersprungen: {e}", key="read")
            self._finish_cycle()
            return
        self._clear("read")
        trend_states = self._trend_states(active)
        fills = self._order_fills(orders)
        self._book_registry_fills(fills)
        by_id = {o.order_id: o for o in orders}
        for sym in active:
            if self.paused:
                break
            with self._lock:
                system = self.systems.get(sym)
                if system is None or not system.is_on or not system.tradable:
                    continue
                try:
                    self._trade_system(system, positions.get(sym), by_id, trend_states.get(sym, True), fills, decide)
                    self._clear(f"trade:{sym}")
                except BrokerError as e:
                    self._emit("error", f"{sym}: {e}", sym, key=f"trade:{sym}")
                self._save()
            self._publish()
        self._finish_cycle()

    def _trend_states(self, symbols: list[str]) -> dict[str, bool | None]:
        """Trendfilter pro Symbol (Netzwerk, deshalb ausserhalb des Locks)."""
        with self._lock:
            wanted = {sym: self.systems[sym].trend_sma for sym in symbols
                      if sym in self.systems and self.systems[sym].trend_sma}
        states = {}
        for sym, sma in wanted.items():
            state = self.trend(sym, sma) if self.trend else None
            states[sym] = state
            if state is None:
                self._emit("warn", f"{sym}: Trend (SMA{sma}) unbekannt – keine neuen Käufe, kein Trend-Verkauf.", sym,
                           key=f"trend:{sym}")
            else:
                self._clear(f"trend:{sym}")
        return states

    def _order_fills(self, orders: list[Order]) -> dict[str, OrderFill]:
        """Ausführungen nach Order-ID. Abgefragt beim Start und wenn eine Order des Bots bei IBKR fehlt
        (IBKR listet nur Orders der laufenden Sitzung – Fills von früheren Tagen stehen nur hier)."""
        listed = {o.order_id for o in orders}
        with self._lock:
            tracked = {oid for s in self.systems.values() if s.is_on for oid in s.open_order_ids()}
        now = self._clock()
        due = self._last_exec_fetch is None or (
            bool(tracked - listed) and now - self._last_exec_fetch >= EXEC_REFRESH_SECONDS)
        if due:
            self._last_exec_fetch = now
            try:
                fresh = self.broker.get_executions(7)
                self._clear("exec")
            except BrokerError as e:
                self._emit("warn", f"Ausführungen nicht abrufbar: {e}", key="exec")
                fresh = []
            archive = self.executions.merge(fresh) if self.executions else fresh
            self._fills = aggregate(archive)
        return self._fills

    def _qty(self, s: EquitySystem, price: float | None) -> int | None:
        """Stückzahl: fester Dollarbetrag wie im Backtest (gerundet, mind. 1) oder feste Stückzahl."""
        if s.order_usd is None:
            return self.order_qty
        if not price or price <= 0:
            return None
        return max(1, round(s.order_usd / price))

    def _record(self, s: EquitySystem, side: str, qty: float, price: float, kind: str, order: Order | None,
                order_id: str, when: float | None = None, estimated: bool = False, exec_ids=(), note: str = "") -> None:
        if self.journal is None:
            return
        try:
            simulated = s.simulated or (order.simulated if order else False) or str(order_id).startswith("DRY-")
            self.journal.record(s.symbol, side, qty, price, kind, order_id, simulated, when=when, estimated=estimated,
                                exec_ids=exec_ids, note=note)
        except OSError as e:
            self._emit("error", f"Journal nicht schreibbar: {e}", key="journal")

    def _finish_cycle(self) -> None:
        self._status["last_cycle"] = self._clock()
        self._publish()

    def _keepalive(self) -> None:
        now = self._clock()
        if now - self._last_keepalive < KEEPALIVE_SECONDS:
            return
        self._last_keepalive = now
        try:
            self.broker.keepalive()
        except BrokerError as e:
            log.debug("Keep-alive fehlgeschlagen: %s", e)

    def _check_auth(self) -> bool:
        try:
            st = self.broker.auth_status()
        except BrokerError as e:
            self._status.update(authenticated=False, connected=False)
            self._emit("error", f"{e}", key="auth")
            return False
        self._status.update(authenticated=st.get("authenticated"), connected=st.get("connected"))
        if not st.get("authenticated"):
            hint = " (Competing Session: bist du gleichzeitig in TWS/der IBKR-App eingeloggt?)" if st.get("competing") else ""
            self._emit("warn", f"IBKR-Gateway nicht eingeloggt. {LOGIN_HINT}{hint}", key="auth")
            return False
        self._emit("info", "Offline-Modus (ohne IBKR), Preise von Stooq/Yahoo." if st.get("offline") else "IBKR-Session aktiv.", key="auth")
        return True

    def _refresh_cash(self) -> None:
        try:
            self._status["cash"] = self.broker.get_cash()
        except BrokerError as e:
            log.debug("Cash nicht lesbar: %s", e)

    def _refresh_quotes(self, symbols: list[str]) -> None:
        for sym in symbols:
            quote = self.marketdata.get_quote(sym)
            if quote is not None:
                self.quotes[sym] = quote
                self._clear(f"quote:{sym}")
            else:
                self._emit("warn", f"{sym}: kein Preis (weder IBKR noch Stooq/Yahoo).", sym, key=f"quote:{sym}")

    # ----------------------------------------------------------- Strategie
    def _trade_system(self, s: EquitySystem, pos: Position | None, by_id: dict[str, Order],
                      trend_ok: bool | None = True, fills: dict[str, OrderFill] | None = None,
                      decide: bool = True) -> None:
        """Eine Runde für ein System. Zuerst IMMER die eigenen Orders abgleichen, dann (nur wenn `decide`)
        handeln: Trend-Verkauf, Einstieg, Levels. Fremde Bestände und fremde Orders werden nie angefasst."""
        fills = fills or {}
        broker_qty = pos.qty if pos else 0.0
        s.position = broker_qty
        self._sync_entry(s, by_id, fills)
        self._sync_levels(s, by_id, fills)
        if self.infer_missing:
            self._infer_missing_fills(s, pos, set(by_id), fills)
        if s.exit_order_id:
            self._sync_exit(s, by_id, fills, broker_qty)
            if s.exit_order_id:
                return
        if not self._plausibility(s, broker_qty):
            return  # eigene Stück fehlen beim Broker: dieses Symbol nicht weiter handeln, bis es geklärt ist
        if not decide or self.paused:
            return
        ok = True if s.trend_sma is None else trend_ok
        if s.trend_exit and ok is False:
            self._trend_exit(s, by_id, broker_qty)
            return
        allow_new = ok is True  # neue Käufe nur bei bekanntem Aufwärtstrend (wie im Backtest)
        if s.entry_price is None:
            if s.entry_order_id is None and allow_new:
                self._open_entry(s)
            return
        if allow_new:
            self._place_levels(s)

    # --- Abgleich -------------------------------------------------------
    def _done(self, order_id: str, by_id: dict[str, Order], fills: dict[str, OrderFill]):
        """Zustand einer eigenen Order: ('open'|'filled'|'cancelled'|'unknown', Menge, Preis, Zeit)."""
        o, f = by_id.get(order_id), fills.get(order_id)
        if o is not None:
            if o.is_filled:
                return ("filled", o.filled_qty or o.qty, o.avg_fill_price or (f.price if f else None),
                        o.filled_at or (f.ts if f else None))
            if o.is_cancelled:
                if f is not None:  # teilweise ausgeführt, Rest storniert
                    return "filled", f.qty, f.price, f.ts
                if o.filled_qty:
                    return "filled", o.filled_qty, o.avg_fill_price, o.filled_at
                return "cancelled", 0.0, None, None
            return "open", 0.0, None, None
        if f is not None:
            reg = self.registry.get(order_id) if self.registry is not None else None
            if reg is None or f.qty >= reg.qty - 1e-9:
                return "filled", f.qty, f.price, f.ts
            return "open", 0.0, None, None  # erst teilweise ausgeführt
        return "unknown", 0.0, None, None

    def _fill_price(self, s: EquitySystem, price: float | None, fallback: float | None) -> tuple[float, bool]:
        """(Preis, geschätzt?) – ohne gemeldeten Preis: Limitpreis bzw. letzter Kurs."""
        if price:
            return float(price), False
        quote = self.quotes.get(s.symbol)
        return float(fallback or (quote.price if quote else 0.0)), True

    def _sync_entry(self, s: EquitySystem, by_id: dict[str, Order], fills: dict[str, OrderFill]) -> None:
        if s.entry_price is not None or not s.entry_order_id:
            return
        state, qty, price, ts = self._done(s.entry_order_id, by_id, fills)
        if state == "filled":
            price, estimated = self._fill_price(s, price, None)
            s.entry_price, s.entry_qty, s.entry_ts = round(price, 2), qty, ts or self._clock()
            self._book(s, "BUY", qty, price, "entry", s.entry_order_id, ts, estimated, fills)
            self._emit("order", f"{s.symbol}: Einstieg gefüllt ({qty:g} @ {price:.2f}), Einstiegspreis {s.entry_price:.2f}.",
                       s.symbol)
            s.entry_order_id = s.entry_order_time = None
        elif state == "cancelled":
            self._emit("warn", f"{s.symbol}: Einstiegs-Order {s.entry_order_id} storniert – neuer Versuch beim nächsten Lauf.",
                       s.symbol)
            s.entry_order_id = s.entry_order_time = None
        elif state == "unknown" and self._clock() - (s.entry_order_time or 0) > ENTRY_ORDER_TIMEOUT:
            self._emit("warn", f"{s.symbol}: Einstiegs-Order {s.entry_order_id} weder offen noch ausgeführt auffindbar – "
                               "neuer Versuch beim nächsten Lauf.", s.symbol)
            s.entry_order_id = s.entry_order_time = None

    def _sync_levels(self, s: EquitySystem, by_id: dict[str, Order], fills: dict[str, OrderFill]) -> None:
        for lv in s.levels:
            if lv.status != PLACED or not lv.order_id:
                continue
            state, qty, price, ts = self._done(lv.order_id, by_id, fills)
            if state == "filled":
                price, estimated = self._fill_price(s, price, lv.price)
                lv.status, lv.qty = FILLED, qty
                self._book(s, "BUY", qty, price, "level", lv.order_id, ts, estimated, fills)
                when = time.strftime("%d.%m.", time.gmtime(ts)) if ts else "heute"
                self._emit("order", f"{s.symbol}: Level {lv.level} gefüllt am {when} ({qty:g} @ {price:.2f}).", s.symbol)
            elif state == "cancelled":
                lv.status = CANCELLED
                self._emit("warn", f"{s.symbol}: Level {lv.level} wurde storniert – wird nicht neu platziert.", s.symbol)

    def _sync_exit(self, s: EquitySystem, by_id: dict[str, Order], fills: dict[str, OrderFill], broker_qty: float) -> None:
        state, qty, price, ts = self._done(s.exit_order_id, by_id, fills)
        if state == "filled":
            price, estimated = self._fill_price(s, price, None)
            self._book(s, "SELL", qty, price, "exit", s.exit_order_id, ts, estimated, fills)
            s.sold_qty += qty
            s.exit_order_id = s.exit_order_time = None
            if s.bot_qty() <= 0.5:
                simulated = s.simulated
                s.reset_trading_state()
                s.simulated = simulated
                self._emit("order", f"{s.symbol}: Verkauf gefüllt ({qty:g} @ {price:.2f}) – neuer Zyklus bei Aufwärtstrend.",
                           s.symbol)
            else:
                self._emit("warn", f"{s.symbol}: Teilverkauf {qty:g} @ {price:.2f}, {s.bot_qty():g} eigene Stück offen – "
                                   "Rest beim nächsten Lauf.", s.symbol)
        elif state == "cancelled":
            self._emit("warn", f"{s.symbol}: Verkaufs-Order {s.exit_order_id} storniert – neuer Versuch beim nächsten Lauf.",
                       s.symbol)
            s.exit_order_id = s.exit_order_time = None
        elif state == "unknown" and self._clock() - (s.exit_order_time or 0) > ENTRY_ORDER_TIMEOUT:
            self._emit("error", f"{s.symbol}: Verkaufs-Order {s.exit_order_id} nicht bestätigbar (Broker hält {broker_qty:g}) – "
                                "bitte im IBKR-Portal prüfen.", s.symbol, key=f"exit:{s.symbol}")

    def _plausibility(self, s: EquitySystem, broker_qty: float) -> bool:
        """Eigene Fills vs. Broker-Bestand. Fremde Stück sind erlaubt; fehlende eigene Stück sind ein Alarm und
        sperren das Symbol (False), bis es geklärt ist (z.B. mit `forward_test.py close`)."""
        own = s.bot_qty()
        if own - broker_qty > 0.5:
            self._emit("error", f"{s.symbol}: Konto hält {broker_qty:g} Stück, eigene Fills ergeben {own:g} – Abweichung "
                                "(Firmenereignis? manueller Verkauf?). Symbol wird nicht weiter gehandelt, bitte prüfen.",
                       s.symbol, key=f"plaus:{s.symbol}")
            return False
        if broker_qty - own > 0.5:
            self._emit("info", f"{s.symbol}: {broker_qty - own:g} fremde Stück im Konto – der Bot fasst sie nicht an.",
                       s.symbol, key=f"plaus:{s.symbol}")
        else:
            self._clear(f"plaus:{s.symbol}")
        return True

    def _book(self, s: EquitySystem, side: str, qty: float, price: float, kind: str, order_id: str,
              when: float | None, estimated: bool, fills: dict[str, OrderFill], note: str = "") -> bool:
        """Journal-Eintrag genau einmal pro Order (idempotent)."""
        if order_id in self._booked:
            return False
        f = fills.get(order_id)
        self._record(s, side, qty, price, kind, None, order_id, when=when, estimated=estimated,
                     exec_ids=f.exec_ids if f else (), note=note)
        self._booked.add(order_id)
        return True

    def _book_registry_fills(self, fills: dict[str, OrderFill]) -> None:
        """Ausführungen eigener Orders, die kein System mehr verfolgt (z.B. Level gefüllt, als der Bot aus war,
        danach Verkauf) – nachbuchen. Ausführungen fremder Orders werden nie gebucht."""
        if self.registry is None or not fills:
            return
        registry = self.registry.all()
        with self._lock:
            tracked = {oid for s in self.systems.values() for oid in s.open_order_ids()}
            for f in sorted(fills.values(), key=lambda x: x.ts):
                reg = registry.get(f.order_id)
                s = self.systems.get(f.symbol)
                if reg is None or s is None or f.order_id in self._booked or f.order_id in tracked:
                    continue
                self._book(s, f.side, f.qty, f.price, reg.kind, f.order_id, f.ts, False, fills)
                self._emit("warn", f"{f.symbol}: Ausführung {f.side} {f.qty:g} @ {f.price:.2f} vom "
                                   f"{time.strftime('%d.%m.', time.gmtime(f.ts))} nachgebucht (Order {f.order_id}).", f.symbol)

    def _infer_missing_fills(self, s: EquitySystem, pos: Position | None, listed: set[str],
                             fills: dict[str, OrderFill]) -> None:
        """Nur für die einmalige Migration: eigene Levels, deren Ausführung bei IBKR nicht mehr abrufbar ist
        (> 7 Tage), aus der Position ableiten. Tag aus Tagestiefs geschätzt, im Journal als geschätzt markiert."""
        accounted = s.accounted_qty()
        if accounted is None or pos is None or s.exit_order_id:
            return
        excess = pos.qty - accounted
        if excess < 0.5:
            return
        for lv in sorted(s.levels, key=lambda x: x.level):
            if lv.status != PLACED or not lv.order_id or lv.order_id in listed or lv.order_id in fills:
                continue
            reg = self.registry.get(lv.order_id) if self.registry is not None else None
            expected = reg.qty if reg is not None else self._qty(s, lv.price)
            if expected is None or expected > excess + 1e-9:
                break
            lv.status, lv.qty = FILLED, float(expected)
            excess -= expected
            when = self._estimate_fill_ts(s, lv.price)
            self._book(s, "BUY", expected, lv.price, "level", lv.order_id, when, True, fills,
                       note="aus Position abgeleitet, Ausführung bei IBKR nicht mehr abrufbar")
            self._emit("warn", f"{s.symbol}: Level {lv.level} aus der Position abgeleitet ({expected:g} @ {lv.price:.2f}, "
                               f"Tag geschätzt {time.strftime('%d.%m.', time.gmtime(when))}).", s.symbol)

    def _estimate_fill_ts(self, s: EquitySystem, price: float) -> float:
        """Erster Handelstag nach dem Einstieg, an dem das Tagestief den Limitpreis erreichte (17:00 UTC)."""
        if self.bars is not None:
            try:
                bars = self.bars(s.symbol)
            except Exception:  # Schätzung ist optional
                bars = []
            since = time.strftime("%Y-%m-%d", time.gmtime(s.entry_ts)) if s.entry_ts else ""
            for b in bars:
                if b.day >= since and b.low <= price:
                    return float(calendar.timegm(time.strptime(b.day + " 17:00", "%Y-%m-%d %H:%M")))
        return self._clock()

    # --- Handeln ---------------------------------------------------------
    def _place(self, s: EquitySystem, kind: str, side: str, qty: int, price: float | None = None,
               level: int | None = None) -> str | None:
        """Order platzieren und im Register merken. None = keine Handelsberechtigung (System wird gesperrt)."""
        try:
            if price is None:
                result = self.broker.place_market_order(s.symbol, side, qty)
            else:
                result = self.broker.place_limit_order(s.symbol, side, qty, price)
        except NotTradableError as e:
            s.not_tradable = True
            self._emit("error", f"{s.symbol}: keine Handelsberechtigung – System wird nicht mehr gehandelt ({e}).", s.symbol)
            return None
        for msg in result.messages:
            if msg != "DRY_RUN":
                self._emit("warn", f"{s.symbol}: IBKR-Hinweis zu Order {result.order_id}: {msg}", s.symbol)
        s.simulated = s.simulated or result.order_id.startswith("DRY-")
        if self.registry is not None:
            self.registry.add(BotOrder(result.order_id, s.symbol, kind, side.upper(), float(qty), level, price, self._clock()))
        return result.order_id

    def _open_entry(self, s: EquitySystem) -> None:
        quote = self.quotes.get(s.symbol)
        qty = self._qty(s, quote.price if quote else None)
        if qty is None:
            self._emit("warn", f"{s.symbol}: kein Preis für die Ordergrösse – Einstieg beim nächsten Lauf.", s.symbol,
                       key=f"size:{s.symbol}")
            return
        order_id = self._place(s, ENTRY, "BUY", qty)
        if order_id:
            s.entry_order_id, s.entry_order_time = order_id, self._clock()
            self._emit("order", f"{s.symbol}: Einstieg Market-Buy {qty} platziert – ID {order_id}.", s.symbol)

    def _place_levels(self, s: EquitySystem) -> None:
        s.ensure_levels()
        for lv in s.levels:
            if lv.status != PENDING:
                continue
            qty = self._qty(s, lv.price)
            order_id = self._place(s, LEVEL, "BUY", qty, lv.price, lv.level)
            if order_id is None:
                return
            lv.status, lv.order_id = PLACED, order_id
            self._emit("order", f"{s.symbol}: Limit-Buy {qty} @ {lv.price:.2f} (Level {lv.level}) platziert – ID {order_id}.",
                       s.symbol)

    def _trend_exit(self, s: EquitySystem, by_id: dict[str, Order], broker_qty: float) -> None:
        """Trend gebrochen: eigene offene Orders stornieren, eigene Stück per Market verkaufen (Backtest: Verkauf zum Open).
        Verkauft wird höchstens min(eigene Stück, Bestand beim Broker) – nie fremde Aktien."""
        had_state = s.entry_price is not None or s.entry_order_id is not None or bool(s.levels)
        to_cancel = [lv.order_id for lv in s.levels if lv.status == PLACED and lv.order_id]
        if s.entry_order_id and s.entry_price is None:
            to_cancel.append(s.entry_order_id)
        for order_id in to_cancel:
            o = by_id.get(order_id)
            if o is None or o.is_open:
                try:
                    self.broker.cancel_order(order_id)
                    self._emit("order", f"{s.symbol}: Trend unter SMA{s.trend_sma} – Order {order_id} storniert.", s.symbol)
                except BrokerError as e:
                    self._emit("error", f"{s.symbol}: Storno von {order_id} fehlgeschlagen: {e} – bitte im Portal prüfen.",
                               s.symbol)
        for lv in s.levels:
            if lv.status == PLACED:
                lv.status = CANCELLED
        s.entry_order_id = s.entry_order_time = None if s.entry_price is None else s.entry_order_id
        qty = int(min(s.bot_qty(), max(broker_qty, 0.0)))
        if qty > 0:
            order_id = self._place(s, EXIT, "SELL", qty)
            if order_id:
                s.exit_order_id, s.exit_order_time = order_id, self._clock()
                self._emit("order", f"{s.symbol}: Trend unter SMA{s.trend_sma} – Market-Sell {qty} platziert – ID {order_id}.",
                           s.symbol)
        elif had_state:
            if s.bot_qty() > 0.5:
                self._emit("error", f"{s.symbol}: {s.bot_qty():g} eigene Stück nicht im Konto – nichts verkauft, Zyklus "
                                    "zurückgesetzt. Bitte prüfen.", s.symbol)
            s.reset_trading_state()
            self._emit("info", f"{s.symbol}: Trend unter SMA{s.trend_sma} – keine eigenen Stück, warte auf Aufwärtstrend.",
                       s.symbol)
