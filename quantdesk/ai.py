"""AI Portfolio Manager mit Claude – analysiert nur, platziert niemals Orders."""
from __future__ import annotations

import json
import re
import threading
from typing import Callable

import anthropic

from quantdesk.broker.base import Broker, BrokerError

SYSTEM_PROMPT = """You are an AI Portfolio Manager responsible for analyzing my portfolio.
Your tasks are the following:
1.) Evaluate risk exposures of my current holdings
2.) Analyze my open limit orders and their potential impact
3.) provide insights into portfolio health, diversification, trade adj. etc.
4.) Speculate on the market outlook based on current market conditions
5.) Identify potential market risks and suggest risk management strategies

This is a paper-trading account; you only analyze, you never place orders.
The strategy running on this account is a Martingale/DCA grid: after one market buy,
limit buys are staggered below the entry price at entry * (1 - drawdown * i).
Entries marked "simulated" come from DRY_RUN mode and do not exist at the broker.
Answer in the language of the question."""

HISTORY_MESSAGES = 6
MAX_TOKENS = 16000
NEWS_PER_SYMBOL = 5

_ADAPTIVE_RE = re.compile(r"^claude-(opus|sonnet|fable|mythos)-(\d+)(?:-(\d+))?")


def supports_adaptive_thinking(model: str) -> bool:
    """Adaptive Thinking gibt es ab Opus/Sonnet 4.6 (Haiku und ältere Modelle: nein)."""
    m = _ADAPTIVE_RE.match(model)
    if not m:
        return False
    major, minor = int(m.group(2)), int(m.group(3) or 0)
    if minor > 20:  # Datums-Suffix wie claude-sonnet-4-20250514, kein Minor
        minor = 0
    return (major, minor) >= (4, 6)


class PortfolioManager:
    def __init__(
        self,
        broker: Broker,
        systems_fn: Callable[[], list[dict]],
        model: str,
        news_enabled: bool = True,
        news_fn: Callable[[str, int], list[dict]] | None = None,
        client: anthropic.Anthropic | None = None,
    ):
        self.broker = broker
        self.systems_fn = systems_fn
        self.model = model
        self.news_enabled = news_enabled
        if news_fn is None:
            from quantdesk.news import fetch_headlines

            news_fn = fetch_headlines
        self.news_fn = news_fn
        self._client = client
        self.history: list[dict] = []
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- Kontext
    def build_context(self) -> str:
        parts = []
        positions = []
        try:
            positions = self.broker.get_positions()
            parts.append("Positions:\n" + json.dumps([p.to_dict() for p in positions], indent=1))
        except BrokerError as e:
            parts.append(f"Positions: unavailable ({e})")
        try:
            orders = self.broker.get_open_orders()
            parts.append("Open orders:\n" + json.dumps([o.to_dict() for o in orders], indent=1))
        except BrokerError as e:
            parts.append(f"Open orders: unavailable ({e})")
        try:
            cash = self.broker.get_cash()
            parts.append(f"Cash (base currency): {cash if cash is not None else 'unknown'}")
        except BrokerError as e:
            parts.append(f"Cash: unavailable ({e})")

        grid = [
            {
                "symbol": s["symbol"],
                "system": s["status"],
                "entry_price": s["entry_price"],
                "drawdown_per_level": s["drawdown"],
                "num_levels": s["num_levels"],
                "levels": [{"level": lv["level"], "price": lv["price"], "status": lv["status"]} for lv in s["levels"]],
                "simulated": s.get("simulated", False),
            }
            for s in self.systems_fn()
        ]
        parts.append("Grid systems (bot configuration):\n" + json.dumps(grid, indent=1))

        if self.news_enabled:
            news_lines = []
            for sym in sorted({p.symbol for p in positions if p.qty}):
                headlines = self.news_fn(sym, NEWS_PER_SYMBOL)
                for h in headlines:
                    news_lines.append(f"- [{sym}] {h['title']} ({h.get('published', '')})")
            if news_lines:
                parts.append("Latest Yahoo Finance headlines:\n" + "\n".join(news_lines))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------- Chat
    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()  # liest ANTHROPIC_API_KEY
        return self._client

    def ask(self, question: str) -> str:
        """Gibt immer einen lesbaren Text zurück – auch bei Fehlern (nie eine Exception)."""
        with self._lock:
            try:
                context = self.build_context()
            except Exception as e:  # Kontext darf den Chat nicht verhindern
                context = f"(Portfolio data unavailable: {e})"
            user_msg = f"Here is my current portfolio data:\n\n{context}\n\nMy question: {question}"
            messages = self.history[-HISTORY_MESSAGES:] + [{"role": "user", "content": user_msg}]
            kwargs = {}
            if supports_adaptive_thinking(self.model):
                kwargs["thinking"] = {"type": "adaptive"}
            try:
                response = self._get_client().messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    **kwargs,
                )
            except anthropic.AuthenticationError:
                return "Fehler: ANTHROPIC_API_KEY fehlt oder ist ungültig (siehe .env)."
            except anthropic.NotFoundError:
                return f"Fehler: Modell '{self.model}' nicht verfügbar – ANTHROPIC_MODEL in .env ändern (z.B. claude-opus-5-5)."
            except anthropic.RateLimitError:
                return "Fehler: Rate-Limit erreicht – kurz warten und nochmal fragen."
            except anthropic.APIStatusError as e:
                return f"Fehler von der Claude-API (HTTP {e.status_code}): {e.message}"
            except anthropic.APIConnectionError:
                return "Fehler: Claude-API nicht erreichbar (Internet?)."
            except anthropic.AnthropicError as e:
                return f"Fehler: {e} – ist ANTHROPIC_API_KEY in .env gesetzt?"

            if response.stop_reason == "refusal":
                return "Claude hat die Anfrage abgelehnt."
            answer = "".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "max_tokens":
                answer += "\n[Antwort abgeschnitten]"
            # Im Verlauf nur die Frage speichern (nicht den ganzen Kontext) – spart Tokens
            self.history += [{"role": "user", "content": question}, {"role": "assistant", "content": answer or "(leer)"}]
            self.history = self.history[-HISTORY_MESSAGES:]
            return answer or "(Claude hat keinen Text geliefert.)"
