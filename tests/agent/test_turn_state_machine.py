from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.session.state import (
    TURN_STAGE_AWAITING_MODEL,
    TURN_STAGE_AWAITING_TOOLS,
    TURN_STAGE_COMPLETED,
    TURN_STAGE_FINALIZING,
    TURN_STAGE_INTERRUPTED,
)


def _make_full_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")


def test_compute_resume_action_for_state_machine_paths(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)

    assert loop.compute_resume_action(
        {
            "current_stage": TURN_STAGE_AWAITING_TOOLS,
            "declared_tool_calls": [{"id": "call_1"}],
            "completed_tool_results": [],
        }
    ) == "await_tools"
    assert loop.compute_resume_action(
        {
            "current_stage": TURN_STAGE_FINALIZING,
            "declared_tool_calls": [],
            "completed_tool_results": [{"tool_call_id": "call_1"}],
        }
    ) == "tail_finalize"
    assert loop.compute_resume_action(
        {
            "current_stage": TURN_STAGE_INTERRUPTED,
            "user_message_ref": "message:msg_1",
            "declared_tool_calls": [],
            "completed_tool_results": [],
            "injected_messages": [],
        }
    ) == "replan"
    assert loop.compute_resume_action(
        {
            "current_stage": TURN_STAGE_INTERRUPTED,
            "user_message_ref": "message:msg_1",
            "declared_tool_calls": [],
            "completed_tool_results": [],
            "injected_messages": [{"message_ref": "message:msg_2"}],
        }
    ) == "request_model_again"


def test_turn_state_advances_through_expected_stages(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    turn_state = loop.create_turn_state("cli:direct")

    turn_state = loop._advance_turn_state(turn_state, current_stage=TURN_STAGE_AWAITING_MODEL)
    assert turn_state["current_stage"] == TURN_STAGE_AWAITING_MODEL

    turn_state = loop._advance_turn_state(
        turn_state,
        current_stage=TURN_STAGE_AWAITING_TOOLS,
        declared_tool_calls=[{"id": "call_1"}],
    )
    assert turn_state["current_stage"] == TURN_STAGE_AWAITING_TOOLS
    assert turn_state["resume_action"] == "await_tools"

    turn_state = loop._advance_turn_state(
        turn_state,
        current_stage=TURN_STAGE_FINALIZING,
        completed_tool_results=[{"tool_call_id": "call_1", "role": "tool", "name": "read_file", "content": "ok"}],
    )
    assert turn_state["current_stage"] == TURN_STAGE_FINALIZING
    assert turn_state["resume_action"] == "tail_finalize"

    loop.finalize_turn(loop.sessions.get_or_create("cli:direct"), turn_state)
    latest = loop.sessions.load_latest_turn_state("cli:direct")
    assert latest is not None
    assert latest["current_stage"] == TURN_STAGE_COMPLETED
    assert loop.sessions.load_active_turn_state("cli:direct") is None


def test_register_turn_message_bumps_injection_revision(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:direct")
    turn_state = loop.create_turn_state("cli:direct")
    turn_state = loop._register_turn_message(
        session,
        turn_state,
        role="user",
        content="first message",
        is_injection=False,
    )

    revision_before = turn_state["revision"]
    injection_before = turn_state["injection_revision"]
    turn_state = loop._register_turn_message(
        session,
        turn_state,
        role="user",
        content="follow-up",
        is_injection=True,
    )

    assert turn_state["revision"] == revision_before + 1
    assert turn_state["injection_revision"] == injection_before + 1
    assert turn_state["injected_messages"][-1]["content"] == "follow-up"
    assert turn_state["resume_action"] == "request_model_again"


@pytest.mark.asyncio
async def test_process_message_creates_and_finalizes_turn_state(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(return_value=(
        "done",
        None,
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "done"},
        ],
        "stop",
        False,
    ))  # type: ignore[method-assign]

    result = await loop._process_message(
        InboundMessage(channel="cli", sender_id="u1", chat_id="direct", content="hello")
    )

    assert result is not None
    assert result.content == "done"
    assert loop.sessions.load_active_turn_state("cli:direct") is None
    latest = loop.sessions.load_latest_turn_state("cli:direct")
    assert latest is not None
    assert latest["current_stage"] == TURN_STAGE_COMPLETED
    assert latest["user_message_ref"].startswith("message:")
