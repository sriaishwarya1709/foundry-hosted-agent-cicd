"""Contoso support desk: a multi-agent Microsoft Agent Framework workflow.

triage -> (billing | technical | general) specialist -> reviewer
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from agent_framework import (
    Agent,
    AgentExecutor,
    AgentExecutorResponse,
    Case,
    Default,
    Message,
    WorkflowBuilder,
)

logger = logging.getLogger("support_desk.workflow")

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SPECIALISTS = ("billing", "technical")
FALLBACK_CATEGORY = "general"
CATEGORIES = (*SPECIALISTS, FALLBACK_CATEGORY)
_CATEGORY_PATTERN = re.compile(r"^\s*CATEGORY\s*:\s*([A-Za-z]+)", re.IGNORECASE | re.MULTILINE)


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def parse_category(text: str | None) -> str:
    match = _CATEGORY_PATTERN.search(text or "")
    category = match.group(1).lower() if match else FALLBACK_CATEGORY
    return category if category in CATEGORIES else FALLBACK_CATEGORY


def routes_to(category: str) -> Callable[[Any], bool]:
    def condition(message: Any) -> bool:
        if not isinstance(message, AgentExecutorResponse):
            return False
        selected = parse_category(message.agent_response.text)
        logger.info("Triage routed request to %s", selected)
        return selected == category

    return condition


def _role(message: Message) -> str:
    return str(getattr(message.role, "value", message.role))


def _last_text(messages: Sequence[Message], role: str) -> str:
    for message in reversed(messages):
        if _role(message) == role and message.text:
            return message.text
    return ""


def _transcript(messages: Sequence[Message]) -> str:
    return "\n".join(f"{_role(message)}: {message.text}" for message in messages if message.text)


def specialist_context(messages: list[Message]) -> list[Message]:
    # Collapse the history into one user turn so smaller models don't have to continue an assistant turn.
    request = _last_text(messages, "user")
    triage = _last_text(messages, "assistant")
    last_user = max((i for i, message in enumerate(messages) if _role(message) == "user"), default=0)
    earlier = _transcript(messages[:last_user])
    history = f"Earlier conversation:\n{earlier}\n\n" if earlier else ""
    return [Message("user", [f"{history}Customer request:\n{request}\n\nTriage notes:\n{triage}"])]


def reviewer_context(messages: list[Message]) -> list[Message]:
    request = _last_text(messages, "user")
    draft = _last_text(messages, "assistant")
    return [Message("user", [f"{request}\n\nDraft reply to review:\n{draft}"])]


def build_workflow_agent(client: Any) -> Any:
    """Build the support-desk workflow and expose it as a single agent."""

    def agent(name: str) -> Agent:
        return Agent(client=client, name=name, instructions=load_prompt(name))

    triage = AgentExecutor(agent("triage"), id="triage")
    specialists = {
        category: AgentExecutor(
            agent(category),
            id=category,
            context_mode="custom",
            context_filter=specialist_context,
        )
        for category in CATEGORIES
    }
    reviewer = AgentExecutor(
        agent("reviewer"),
        id="reviewer",
        context_mode="custom",
        context_filter=reviewer_context,
    )

    builder = WorkflowBuilder(
        name="contoso-support-desk",
        description="Triage, specialist drafting, and QA review for customer support requests.",
        start_executor=triage,
        output_from=[reviewer],
    ).add_switch_case_edge_group(
        triage,
        [
            *(Case(condition=routes_to(category), target=specialists[category]) for category in SPECIALISTS),
            Default(target=specialists[FALLBACK_CATEGORY]),
        ],
    )
    for specialist in specialists.values():
        builder = builder.add_edge(specialist, reviewer)

    return builder.build().as_agent()
