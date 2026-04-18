from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.runner import AgentRunResult
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse, ToolCallRequest


def _make_loop(tmp_path: Path) -> tuple[AgentLoop, MagicMock]:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    return loop, provider


def _content_contains(messages: list[dict], needle: str) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and needle in content:
            return True
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and needle in str(block.get("text", "")):
                    return True
    return False


@pytest.mark.asyncio
async def test_structured_context_upgrade_e2e(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("structured state keeps artifacts stable\n", encoding="utf-8")

    loop, provider = _make_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)  # type: ignore[method-assign]

    model_calls: list[list[dict]] = []
    call_count = 0

    async def scripted_chat_with_retry(*, messages, **_kwargs):
        nonlocal call_count
        call_count += 1
        model_calls.append(messages)
        if call_count == 1:
            return LLMResponse(
                content="Inspect workspace first.",
                tool_calls=[ToolCallRequest(id="call_glob", name="glob", arguments={"pattern": "*.txt"})],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        if call_count == 2:
            return LLMResponse(
                content="Read the matching file.",
                tool_calls=[ToolCallRequest(id="call_read", name="read_file", arguments={"path": "note.txt"})],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        if call_count == 3:
            return LLMResponse(
                content="First turn complete.",
                tool_calls=[],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        if call_count == 4:
            return LLMResponse(
                content="Recovered and finished.",
                tool_calls=[],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        raise AssertionError(f"unexpected model call #{call_count}")

    provider.chat_with_retry = scripted_chat_with_retry
    provider.chat_stream_with_retry = scripted_chat_with_retry

    first = await loop.process_direct("先检查工作区，再读取 note.txt", session_key="cli:e2e")

    assert first is not None
    assert first.content == "First turn complete."
    assert call_count == 3
    assert not any(_content_contains(messages, "[Resumed Session]") for messages in model_calls)

    latest_turn = loop.sessions.load_latest_turn_state("cli:e2e")
    assert latest_turn is not None
    assert latest_turn["current_stage"] == "completed"
    assert latest_turn["commit_state"] == "committed"
    manifest = loop.sessions.resolve_ref("cli:e2e", latest_turn["commit_manifest_ref"])
    assert manifest is not None
    assert manifest["completed_marker"] is True
    assert len(manifest["artifact_refs"]) >= 2
    assert loop.sessions.load_active_turn_state("cli:e2e") is None
    assert loop.sessions.get_or_create("cli:e2e").metadata == {}

    stable_working_set = loop.sessions.load_latest_working_set("cli:e2e")
    assert stable_working_set is not None
    assert stable_working_set["version"] == latest_turn["working_set_version"]

    await loop.auto_compact._archive("cli:e2e")

    candidate = loop.sessions.read_state_index("cli:e2e", "autocompact-candidate")
    assert candidate is not None
    candidate_snapshot = loop.sessions.load_working_set("cli:e2e", candidate["working_set_version"])
    assert candidate_snapshot is not None
    assert candidate_snapshot["published_by"] == "autocompact_candidate"
    assert candidate_snapshot["is_stable"] is False
    assert loop.sessions.load_latest_working_set("cli:e2e")["version"] == stable_working_set["version"]

    original_chat = provider.chat_with_retry
    provider.chat_with_retry = AsyncMock(return_value=MagicMock(content="dream-analysis"))
    loop.dream._runner.run = AsyncMock(
        return_value=AgentRunResult(
            final_content="dream-updated",
            messages=[],
            tools_used=[],
            usage={},
            stop_reason="completed",
        )
    )

    dream_result = await loop.dream.run()

    assert dream_result is True
    dream_cursor = loop.sessions.read_state_index("cli:e2e", "dream-cursor")
    assert dream_cursor is not None
    assert dream_cursor["processed_count"] == 1

    provider.chat_with_retry = original_chat

    checkpoint_saved = asyncio.Event()
    original_run_agent_loop = loop._run_agent_loop

    async def interrupted_run_agent_loop(_initial_messages, *, session=None, **_kwargs):
        assert session is not None
        loop._sync_turn_state_from_checkpoint(
            session,
            {
                "phase": "awaiting_tools",
                "assistant_message": {
                    "role": "assistant",
                    "content": "Working on recovery.",
                    "tool_calls": [
                        {
                            "id": "call_done",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        },
                        {
                            "id": "call_pending",
                            "type": "function",
                            "function": {"name": "exec", "arguments": "{}"},
                        },
                    ],
                },
                "completed_tool_results": [
                    {
                        "role": "tool",
                        "tool_call_id": "call_done",
                        "name": "read_file",
                        "content": "partial result",
                    }
                ],
                "pending_tool_calls": [
                    {
                        "id": "call_pending",
                        "type": "function",
                        "function": {"name": "exec", "arguments": "{}"},
                    }
                ],
            },
        )
        checkpoint_saved.set()
        await asyncio.Event().wait()

    loop._run_agent_loop = interrupted_run_agent_loop  # type: ignore[method-assign]

    interrupted_task = asyncio.create_task(
        loop.process_direct("开始第二轮并故意中断", session_key="cli:e2e")
    )
    await asyncio.wait_for(checkpoint_saved.wait(), timeout=1.0)
    interrupted_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await interrupted_task

    interrupted_turn = loop.sessions.load_active_turn_state("cli:e2e")
    assert interrupted_turn is not None
    assert interrupted_turn["current_stage"] == "interrupted"
    assert interrupted_turn["resume_action"] == "await_tools"
    assert loop.sessions.get_or_create("cli:e2e").metadata == {}

    loop._run_agent_loop = original_run_agent_loop  # type: ignore[method-assign]

    resumed = await loop.process_direct("继续并完成第二轮", session_key="cli:e2e")

    assert resumed is not None
    assert resumed.content == "Recovered and finished."
    assert call_count == 4
    assert not any(_content_contains(messages, "[Resumed Session]") for messages in model_calls)

    final_session = loop.sessions.get_or_create("cli:e2e")
    assert any(
        message.get("role") == "tool"
        and message.get("tool_call_id") == "call_pending"
        and "interrupted before this tool finished" in str(message.get("content", "")).lower()
        for message in final_session.messages
    )
    assert loop.sessions.load_active_turn_state("cli:e2e") is None
    resumed_latest_turn = loop.sessions.load_latest_turn_state("cli:e2e")
    assert resumed_latest_turn is not None
    assert resumed_latest_turn["current_stage"] == "completed"
    assert resumed_latest_turn["commit_state"] == "committed"
    resumed_manifest = loop.sessions.resolve_ref("cli:e2e", resumed_latest_turn["commit_manifest_ref"])
    assert resumed_manifest is not None
    assert resumed_manifest["completed_marker"] is True
    assert final_session.metadata == {}

    await loop.close_mcp()
