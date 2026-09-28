import asyncio
from collections.abc import Sequence
from typing import Any

import pytest
from agent_framework import BaseChatClient, ChatResponse, ChatResponseUpdate, Message

from workflow import (
    CATEGORIES,
    build_workflow_agent,
    load_prompt,
    parse_category,
    reviewer_context,
    specialist_context,
)


class ScriptedChatClient(BaseChatClient):
    """Fake model that answers according to which agent's instructions it receives."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, list[str]]] = []

    def _reply(self, messages: Sequence[Message], options: Any) -> str:
        texts = [message.text or "" for message in messages]
        instructions = str((options or {}).get("instructions") or "") + " ".join(texts)
        user = next((message.text for message in reversed(messages) if str(message.role) == "user"), "")
        if "triage agent" in instructions:
            agent, category = "triage", "billing" if "refund" in user.lower() else "technical" if "error" in user.lower() else "general"
            reply = f"CATEGORY: {category}\nSUMMARY: needs help\nDETAILS: none"
        elif "quality reviewer" in instructions:
            agent, reply = "reviewer", f"FINAL<{user}>"
        else:
            agent = next(name for name in ("customer care", "billing", "technical") if name in instructions)
            reply = f"DRAFT-{agent}"
        self.calls.append((agent, texts))
        return reply

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        reply = self._reply(messages, options)
        if stream:

            async def _stream():
                yield ChatResponseUpdate(role="assistant", contents=[{"type": "text", "text": reply}])

            return _stream()
        return ChatResponse(messages=[Message(role="assistant", contents=[reply])], response_id="fake")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("CATEGORY: billing\nSUMMARY: x", "billing"),
        ("category: Technical", "technical"),
        ("SUMMARY: x\nCATEGORY: general", "general"),
        ("CATEGORY: sales", "general"),
        ("no category here", "general"),
        (None, "general"),
    ],
)
def test_parse_category(text: str | None, expected: str) -> None:
    assert parse_category(text) == expected


def test_every_agent_has_a_prompt() -> None:
    for name in (*CATEGORIES, "triage", "reviewer"):
        assert load_prompt(name)


def test_specialist_context_collapses_history_into_one_user_turn() -> None:
    history = [
        Message("user", ["I was charged twice"]),
        Message("assistant", ["CATEGORY: billing"]),
    ]

    [message] = specialist_context(history)

    assert str(message.role) == "user"
    assert "I was charged twice" in message.text
    assert "CATEGORY: billing" in message.text
    assert "Earlier conversation" not in message.text


def test_specialist_context_keeps_earlier_turns() -> None:
    history = [
        Message("user", ["I'm on Basic but billed for Pro"]),
        Message("assistant", ["Sorry about that."]),
        Message("user", ["Which plan am I on?"]),
        Message("assistant", ["CATEGORY: billing"]),
    ]

    [message] = specialist_context(history)

    assert "Earlier conversation:\nuser: I'm on Basic but billed for Pro" in message.text
    assert "Customer request:\nWhich plan am I on?" in message.text
    assert message.text.endswith("CATEGORY: billing")


def test_reviewer_context_includes_draft() -> None:
    history = [Message("user", ["Customer request: help"]), Message("assistant", ["Draft text"])]

    [message] = reviewer_context(history)

    assert "Customer request: help" in message.text
    assert "Draft text" in message.text


@pytest.mark.parametrize(
    ("prompt", "specialist"),
    [
        ("Please refund my last invoice", "billing"),
        ("I get error 500 from the API", "technical"),
        ("Hello there", "customer care"),
    ],
)
def test_workflow_routes_to_one_specialist_then_reviewer(prompt: str, specialist: str) -> None:
    client = ScriptedChatClient()
    agent = build_workflow_agent(client)

    response = asyncio.run(agent.run(prompt))

    assert [name for name, _ in client.calls] == ["triage", specialist, "reviewer"]
    assert response.text.startswith("FINAL<")
    assert prompt in response.text
