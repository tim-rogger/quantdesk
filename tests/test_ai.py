from types import SimpleNamespace

import anthropic
import pytest

from quantdesk.ai import HISTORY_MESSAGES, SYSTEM_PROMPT, PortfolioManager, supports_adaptive_thinking
from quantdesk.broker.base import Position
from tests.fakes import FakeBroker


class FakeMessages:
    def __init__(self, text="Alles ok.", error=None, stop_reason="end_turn"):
        self.calls = []
        self.text, self.error, self.stop_reason = text, error, stop_reason

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=self.text)],
        )


def make_pm(messages=None, news=True, model="claude-opus-4-8"):
    b = FakeBroker()
    b.positions["AAPL"] = Position("AAPL", 3, 190.5, 195.0, 13.5)
    b.place_limit_order("AAPL", "BUY", 1, 186.69)
    systems = [{"symbol": "AAPL", "status": "On", "entry_price": 190.5, "drawdown": 0.02, "num_levels": 1,
                "levels": [{"level": 1, "price": 186.69, "status": "placed", "order_id": "O1"}], "simulated": False}]
    fake = SimpleNamespace(messages=messages or FakeMessages())
    news_fn = lambda sym, n: [{"title": f"{sym} beats earnings", "published": "Mon"}]
    return PortfolioManager(b, lambda: systems, model, news_enabled=news, news_fn=news_fn, client=fake), fake.messages


def test_prompt_contains_positions_orders_grid_and_news():
    pm, msgs = make_pm()
    assert pm.ask("Wie riskant ist mein Portfolio?") == "Alles ok."
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-4-8"
    assert call["system"] == SYSTEM_PROMPT
    assert "you never place orders" in call["system"] and "Evaluate risk exposures" in call["system"]
    user = call["messages"][-1]["content"]
    assert '"symbol": "AAPL"' in user and "190.5" in user  # Position
    assert "186.69" in user and '"side": "BUY"' in user  # offene Order mit echter Side
    assert "Cash (base currency): 100000.0" in user
    assert "AAPL beats earnings" in user
    assert user.endswith("My question: Wie riskant ist mein Portfolio?")
    assert call["thinking"] == {"type": "adaptive"}


def test_news_disabled():
    pm, msgs = make_pm(news=False)
    pm.ask("?")
    assert "headlines" not in msgs.calls[0]["messages"][-1]["content"]


def test_history_limited():
    pm, msgs = make_pm()
    for i in range(6):
        pm.ask(f"Frage {i}")
    last = msgs.calls[-1]["messages"]
    assert len(last) == HISTORY_MESSAGES + 1
    assert last[0]["role"] == "user" and last[0]["content"] == "Frage 2"
    assert len(pm.history) == HISTORY_MESSAGES


def test_errors_become_readable_text():
    pm, _ = make_pm(FakeMessages(error=anthropic.AnthropicError("no key")))
    assert pm.ask("?").startswith("Fehler")
    assert pm.history == []


def test_broker_errors_do_not_break_chat():
    pm, msgs = make_pm()
    pm.broker.fail_reads = True
    assert pm.ask("?") == "Alles ok."
    assert "unavailable" in msgs.calls[0]["messages"][-1]["content"]


def test_refusal_and_truncation():
    pm, _ = make_pm(FakeMessages(stop_reason="refusal"))
    assert "abgelehnt" in pm.ask("?")
    pm, _ = make_pm(FakeMessages(stop_reason="max_tokens"))
    assert pm.ask("?").endswith("[Antwort abgeschnitten]")


@pytest.mark.parametrize(
    "model, expected",
    [("claude-opus-4-8", True), ("claude-opus-5-5", True), ("claude-opus-5", True), ("claude-sonnet-4-6", True),
     ("claude-fable-5-1", True), ("claude-haiku-4-5", False), ("claude-sonnet-4-5", False),
     ("claude-sonnet-4-20250514", False), ("gpt-4", False)],
)
def test_adaptive_thinking_support(model, expected):
    assert supports_adaptive_thinking(model) is expected


def test_no_thinking_param_for_haiku():
    pm, msgs = make_pm(model="claude-haiku-4-5")
    pm.ask("?")
    assert "thinking" not in msgs.calls[0]
