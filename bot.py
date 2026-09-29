"""QuantDesk AI Bot – Tkinter-GUI (Einstiegspunkt: python bot.py).

Basiert auf AI_Trading_Bot von Roman Paolucci, umgebaut auf IBKR Paper + Claude.
Tkinter wird nur im Haupt-Thread angefasst: Engine und Claude laufen in Hintergrund-Threads
und melden sich über Queues, die hier per root.after(...) abgeholt werden.
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from quantdesk.app import Services, build_services
from quantdesk.config import load_settings
from quantdesk.engine import Event
from quantdesk.strategy import CANCELLED, FILLED, PENDING, PLACED, ValidationError, parse_grid_input

POLL_MS = 300
LEVEL_MARKS = {PENDING: "○", PLACED: "●", FILLED: "✓", CANCELLED: "✗"}
LOG_COLORS = {"info": "#333333", "order": "#1a6e1a", "warn": "#b36b00", "error": "#c0392b"}
COLUMNS = ("Symbol", "Position", "Entry Price", "Last", "Levels", "Status")


def format_levels(row: dict) -> str:
    if row.get("exit_pending"):
        return "Trend gebrochen – Verkauf läuft…"
    if not row["levels"]:
        if row["entry_pending"]:
            return f"{row['num_levels']} Levels – Einstieg läuft…"
        return f"{row['num_levels']} Levels à {row['drawdown']:.2%} (nach Einstieg)"
    return " ".join(f"{LEVEL_MARKS.get(status, '?')}{price:.2f}" for _, price, status in row["levels"])


def _fmt_price(value) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "–"


class TradingBotGUI:
    def __init__(self, root: tk.Tk, services: Services):
        self.root = root
        self.s = services
        self.engine = services.engine
        self.chat_queue: queue.Queue[str] = queue.Queue()
        self._last_view = None
        mode = services.settings.mode

        root.title(f"QuantDesk AI Bot – {mode}")
        root.minsize(900, 640)

        # --- Modus & Status (gut sichtbar) ---
        header = tk.Frame(root)
        header.pack(fill=tk.X, padx=10, pady=(10, 0))
        if mode == "PAPER":
            text, bg = "PAPER – Orders gehen an dein IBKR-Paper-Konto", "#e67e22"
        else:
            text, bg = "DRY_RUN – es werden KEINE Orders an IBKR gesendet", "#f5c542"
        tk.Label(header, text=text, bg=bg, font=("Segoe UI", 11, "bold"), padx=8, pady=4).pack(side=tk.LEFT)
        self.cash_label = tk.Label(header, text="Cash: –", padx=10)
        self.cash_label.pack(side=tk.RIGHT)
        self.conn_label = tk.Label(header, text="IBKR: prüfe…", padx=10)
        self.conn_label.pack(side=tk.RIGHT)

        # --- Formular: neue Equity ---
        form = tk.Frame(root)
        form.pack(pady=10)
        tk.Label(form, text="Symbol:").grid(row=0, column=0)
        self.symbol_entry = tk.Entry(form, width=10)
        self.symbol_entry.grid(row=0, column=1, padx=(0, 8))
        tk.Label(form, text="Levels:").grid(row=0, column=2)
        self.levels_entry = tk.Entry(form, width=6)
        self.levels_entry.grid(row=0, column=3, padx=(0, 8))
        tk.Label(form, text="Drawdown%:").grid(row=0, column=4)
        self.drawdown_entry = tk.Entry(form, width=6)
        self.drawdown_entry.grid(row=0, column=5, padx=(0, 8))
        tk.Button(form, text="Add Equity", command=self.add_equity).grid(row=0, column=6)

        # --- Tabelle ---
        table = tk.Frame(root)
        table.pack(fill=tk.BOTH, expand=True, padx=10)
        self.tree = ttk.Treeview(table, columns=COLUMNS, show="headings", height=8)
        widths = {"Symbol": 80, "Position": 70, "Entry Price": 90, "Last": 120, "Levels": 380, "Status": 130}
        for col in COLUMNS:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=widths[col], anchor=tk.W if col == "Levels" else tk.CENTER)
        scroll = ttk.Scrollbar(table, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tk.Label(root, text="Levels: ○ ausstehend  ● platziert  ✓ gefüllt  ✗ storniert", fg="#666").pack(anchor=tk.W, padx=10)

        # --- Steuerung ---
        controls = tk.Frame(root)
        controls.pack(pady=5)
        tk.Button(controls, text="Toggle Selected System", command=self.toggle_selected_system).pack(side=tk.LEFT, padx=4)
        tk.Button(controls, text="Remove Selected Equity", command=self.remove_selected_equity).pack(side=tk.LEFT, padx=4)
        tk.Button(
            controls, text="STOP ALL", command=self.stop_all, bg="#c0392b", fg="white", font=("Segoe UI", 10, "bold")
        ).pack(side=tk.LEFT, padx=(20, 4))
        tk.Button(controls, text="Resume Engine", command=self.resume).pack(side=tk.LEFT, padx=4)

        # --- Log ---
        tk.Label(root, text="Bot-Log", anchor=tk.W).pack(fill=tk.X, padx=10)
        self.log_output = scrolledtext.ScrolledText(root, height=7, state=tk.DISABLED, font=("Consolas", 9))
        self.log_output.pack(fill=tk.BOTH, padx=10)
        for level, color in LOG_COLORS.items():
            self.log_output.tag_config(level, foreground=color)

        # --- AI Portfolio Manager ---
        chat = tk.Frame(root)
        chat.pack(pady=(10, 4), padx=10, fill=tk.X)
        tk.Label(chat, text="Frag Claude:").pack(side=tk.LEFT)
        self.chat_input = tk.Entry(chat)
        self.chat_input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.chat_input.bind("<Return>", lambda _e: self.send_message())
        self.send_button = tk.Button(chat, text="Send", command=self.send_message)
        self.send_button.pack(side=tk.LEFT)
        self.chat_output = scrolledtext.ScrolledText(root, height=10, state=tk.DISABLED, wrap=tk.WORD)
        self.chat_output.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.engine.start()
        self.root.after(POLL_MS, self._poll)

    # ------------------------------------------------------------ Hilfen
    def _bg(self, fn, *args) -> None:
        """Engine-Befehle nie im Tk-Thread ausführen (können Netzwerk brauchen)."""

        def run():
            try:
                fn(*args)
            except ValidationError as e:
                self.engine.events.put(Event("warn", str(e)))
            except Exception as e:
                logging.exception("Befehl fehlgeschlagen")
                self.engine.events.put(Event("error", f"Befehl fehlgeschlagen: {e}"))

        threading.Thread(target=run, daemon=True).start()

    def _selected_symbols(self) -> list[str]:
        return [str(self.tree.item(item)["values"][0]) for item in self.tree.selection()]

    def _append(self, widget: tk.Text, text: str, tag: str | None = None) -> None:
        widget.config(state=tk.NORMAL)
        widget.insert(tk.END, text, tag)
        widget.see(tk.END)
        widget.config(state=tk.DISABLED)

    # ------------------------------------------------------------ Buttons
    def add_equity(self) -> None:
        try:
            symbol, levels, drawdown = parse_grid_input(
                self.symbol_entry.get(), self.levels_entry.get(), self.drawdown_entry.get()
            )
        except ValidationError as e:
            messagebox.showerror("Ungültige Eingabe", str(e))
            return
        if any(row["symbol"] == symbol for row in self.engine.view):
            messagebox.showerror("Ungültige Eingabe", f"{symbol} ist schon in der Liste.")
            return
        for entry in (self.symbol_entry, self.levels_entry, self.drawdown_entry):
            entry.delete(0, tk.END)
        self._bg(self.engine.add_system, symbol, levels, drawdown)

    def toggle_selected_system(self) -> None:
        symbols = self._selected_symbols()
        if not symbols:
            messagebox.showwarning("Hinweis", "Keine Equity ausgewählt.")
            return
        self._bg(self.engine.toggle, symbols)

    def remove_selected_equity(self) -> None:
        symbols = self._selected_symbols()
        if not symbols:
            messagebox.showwarning("Hinweis", "Keine Equity ausgewählt.")
            return
        rows = {row["symbol"]: row for row in self.engine.view}
        for symbol in symbols:
            open_orders = rows.get(symbol, {}).get("open_orders", 0)
            cancel = False
            if open_orders:
                answer = messagebox.askyesnocancel(
                    "Equity entfernen",
                    f"{symbol} hat {open_orders} offene Order(s).\n\n"
                    "Ja = Orders stornieren und entfernen\nNein = nur entfernen (Orders bleiben offen)\nAbbrechen = nichts tun",
                )
                if answer is None:
                    continue
                cancel = answer
            elif not messagebox.askyesno("Equity entfernen", f"{symbol} wirklich entfernen?"):
                continue
            self._bg(self.engine.remove, symbol, cancel)

    def stop_all(self) -> None:
        self._bg(self.engine.stop_all)

    def resume(self) -> None:
        self._bg(self.engine.resume)

    def send_message(self) -> None:
        message = self.chat_input.get().strip()
        if not message or str(self.send_button["state"]) == tk.DISABLED:
            return
        self.chat_input.delete(0, tk.END)
        self.send_button.config(state=tk.DISABLED)
        self._append(self.chat_output, f"Du: {message}\n", None)
        self._append(self.chat_output, "Claude denkt nach…\n", None)

        def run():
            self.chat_queue.put(self.s.ai.ask(message))

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ Polling
    def _poll(self) -> None:
        try:
            while True:
                ev: Event = self.engine.events.get_nowait()
                stamp = time.strftime("%H:%M:%S", time.localtime(ev.ts))
                self._append(self.log_output, f"{stamp}  {ev.message}\n", ev.level)
        except queue.Empty:
            pass
        try:
            while True:
                answer = self.chat_queue.get_nowait()
                self._append(self.chat_output, f"Claude: {answer}\n\n", None)
                self.send_button.config(state=tk.NORMAL)
        except queue.Empty:
            pass
        self._refresh_table()
        self._refresh_status()
        self.root.after(POLL_MS, self._poll)

    def _refresh_table(self) -> None:
        view = self.engine.view
        if view is self._last_view:
            return
        self._last_view = view
        symbols = {row["symbol"] for row in view}
        for iid in self.tree.get_children():
            if iid not in symbols:
                self.tree.delete(iid)
        for row in view:
            last = f"{row['last_price']:.2f} ({row['price_source']})" if row["last_price"] else "–"
            values = (
                row["symbol"],
                f"{row['position']:g}",
                _fmt_price(row["entry_price"]),
                last,
                format_levels(row),
                row["status"] + (f" · {row['trend_label']}" if row.get("trend_label") else ""),
            )
            if self.tree.exists(row["symbol"]):
                self.tree.item(row["symbol"], values=values)
            else:
                self.tree.insert("", tk.END, iid=row["symbol"], values=values)

    def _refresh_status(self) -> None:
        st = self.engine.status_view
        if st.get("paused"):
            self.conn_label.config(text="ENGINE PAUSIERT (STOP ALL)", fg="#c0392b")
        elif self.s.offline:
            self.conn_label.config(text="Offline (kein IBKR-Konto in .env) – Preise: Stooq/Yahoo", fg="#555")
        elif st.get("authenticated"):
            self.conn_label.config(text=f"IBKR {self.s.settings.ibkr_account_id}: verbunden ✓", fg="#1a6e1a")
        elif st.get("authenticated") is False:
            self.conn_label.config(text="IBKR: nicht eingeloggt → https://localhost:5000", fg="#c0392b")
        cash = st.get("cash")
        self.cash_label.config(text=f"Cash: {cash:,.2f}" if isinstance(cash, (int, float)) else "Cash: –")

    def on_close(self) -> None:
        self.engine.stop()
        self.root.destroy()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("quantdesk.log", encoding="utf-8")],
    )
    root = tk.Tk()
    try:
        services = build_services(load_settings())
    except ValueError as e:
        root.withdraw()
        messagebox.showerror("QuantDesk – Start verweigert", str(e))
        root.destroy()
        return 1
    app = TradingBotGUI(root, services)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
