"""Tests for stage-6 Dream behavior."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.memory import Dream, MemoryStore
from nanobot.agent.runner import AgentRunResult
from nanobot.agent.skills import BUILTIN_SKILLS_DIR
from nanobot.session.manager import SessionManager
from nanobot.session.state import (
    COMMIT_STATE_COMMITTED,
    TURN_STAGE_COMPLETED,
    build_turn_state,
    make_ref,
)


@pytest.fixture
def store(tmp_path):
    value = MemoryStore(tmp_path)
    value.write_soul("# Soul\n- Helpful")
    value.write_user_rules("# User Rules\n- Reply briefly")
    value.write_user_profile("# User Profile\n- Developer")
    value.write_current("# Current\n- Mirror only")
    return value


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock()
    return provider


@pytest.fixture
def mock_runner():
    return MagicMock()


@pytest.fixture
def dream(store, mock_provider, mock_runner):
    value = Dream(store=store, provider=mock_provider, model="test-model", max_batch_size=5)
    value._runner = mock_runner
    return value


def _make_run_result(stop_reason="completed", final_content=None, tool_events=None):
    return AgentRunResult(
        final_content=final_content or stop_reason,
        stop_reason=stop_reason,
        messages=[],
        tools_used=[],
        usage={},
        tool_events=tool_events or [],
    )


def _persist_committed_turn(
    store: MemoryStore,
    *,
    session_key: str,
    turn_id: str,
    version: int,
    user_goal: str = "Refactor memory system",
) -> None:
    sessions = SessionManager(store.workspace)
    artifact_id = f"artifact_{turn_id}"
    capsule_id = f"capsule_{turn_id}"
    response_id = f"resp_{turn_id}"
    commit_id = f"commit_{turn_id}"
    sessions.save_artifact(
        session_key,
        artifact_id,
        {
            "artifact_id": artifact_id,
            "dream_render": {
                "artifact_id": artifact_id,
                "source_type": "read_file",
                "digest": "sha256:test",
            },
        },
    )
    sessions.save_capsule(
        session_key,
        capsule_id,
        {
            "capsule_id": capsule_id,
            "turn_id": turn_id,
            "session_key": session_key,
            "source_revision": 1,
            "user_goal": user_goal,
            "assistant_intent": "Implement the requested change",
            "decisions": ["Use structured state"],
            "outcomes": ["State pipeline updated"],
            "open_questions": [],
            "artifact_refs": [make_ref("artifact", artifact_id)],
            "next_expected_action": None,
            "capsule_version": 1,
            "created_at": "2026-04-19T10:00:00",
        },
    )
    sessions.save_working_set(
        session_key,
        {
            "session_key": session_key,
            "version": version,
            "source_turn_id": turn_id,
            "source_revision": 1,
            "is_stable": True,
            "published_by": "agent_loop",
            "active_task": None,
            "task_stage": None,
            "active_goals": [user_goal],
            "open_loops": [],
            "last_user_focus": user_goal,
            "relevant_capsule_refs": [make_ref("capsule", capsule_id)],
            "relevant_artifact_refs": [make_ref("artifact", artifact_id)],
            "budget_hints": {},
            "source_turn_ids": [turn_id],
            "created_at": "2026-04-19T10:00:00",
        },
    )
    sessions.save_response_object(
        session_key,
        response_id,
        {
            "response_id": response_id,
            "session_key": session_key,
            "turn_id": turn_id,
            "source_revision": 1,
            "content": "done",
            "created_at": "2026-04-19T10:00:00",
        },
    )
    sessions.save_commit_manifest(
        session_key,
        commit_id,
        {
            "commit_id": commit_id,
            "turn_id": turn_id,
            "session_key": session_key,
            "turn_revision": 1,
            "artifact_refs": [make_ref("artifact", artifact_id)],
            "capsule_ref": make_ref("capsule", capsule_id),
            "working_set_version": version,
            "final_response_ref": make_ref("response", response_id),
            "completed_marker": True,
            "created_at": "2026-04-19T10:00:00",
        },
    )
    turn_state = build_turn_state(session_key=session_key, turn_id=turn_id)
    turn_state.update(
        {
            "revision": 1,
            "current_stage": TURN_STAGE_COMPLETED,
            "commit_state": COMMIT_STATE_COMMITTED,
            "commit_id": commit_id,
            "commit_manifest_ref": make_ref("commit", commit_id),
            "capsule_ref": make_ref("capsule", capsule_id),
            "working_set_version": version,
            "final_response_ref": make_ref("response", response_id),
            "artifact_refs": [make_ref("artifact", artifact_id)],
        }
    )
    sessions.save_turn_state(session_key, turn_id, turn_state)
    sessions.publish_latest_turn(session_key, turn_id)
    sessions.publish_latest_working_set(session_key, version)


def _persist_uncommitted_turn(store: MemoryStore, *, session_key: str, turn_id: str) -> None:
    sessions = SessionManager(store.workspace)
    turn_state = build_turn_state(session_key=session_key, turn_id=turn_id)
    sessions.save_turn_state(session_key, turn_id, turn_state)
    sessions.publish_latest_turn(session_key, turn_id)


class TestDreamRun:
    async def test_noop_when_no_committed_turns(self, dream, mock_provider, mock_runner):
        result = await dream.run()
        assert result is False
        mock_provider.chat_with_retry.assert_not_called()
        mock_runner.run.assert_not_called()

    async def test_dream_only_consumes_committed_turns(self, dream, mock_provider, mock_runner, store):
        _persist_uncommitted_turn(store, session_key="cli:test", turn_id="turn_open")
        _persist_committed_turn(store, session_key="cli:test", turn_id="turn_done", version=1)
        mock_provider.chat_with_retry.return_value = MagicMock(content="candidate memory update")
        mock_runner.run = AsyncMock(return_value=_make_run_result())

        result = await dream.run()

        assert result is True
        phase1_prompt = mock_provider.chat_with_retry.call_args.kwargs["messages"][1]["content"]
        assert "turn_done" in phase1_prompt
        assert "turn_open" not in phase1_prompt
        assert "Mirror only" not in phase1_prompt
        assert "history.jsonl" not in phase1_prompt

    async def test_advances_dream_cursor_and_persists_idempotency(self, dream, mock_provider, mock_runner, store):
        _persist_committed_turn(store, session_key="cli:test", turn_id="turn_one", version=1)
        _persist_committed_turn(store, session_key="cli:test", turn_id="turn_two", version=2)
        mock_provider.chat_with_retry.return_value = MagicMock(content="Nothing new")
        mock_runner.run = AsyncMock(return_value=_make_run_result())

        await dream.run()

        cursor = SessionManager(store.workspace).read_state_index("cli:test", "dream-cursor")
        assert store.get_last_dream_cursor() == 2
        assert cursor["processed_count"] == 2
        assert len(cursor["processed_keys"]) == 2

    async def test_dream_is_idempotent_by_turn_capsule_revision(self, dream, mock_provider, mock_runner, store):
        _persist_committed_turn(store, session_key="cli:test", turn_id="turn_one", version=1)
        mock_provider.chat_with_retry.return_value = MagicMock(content="Nothing new")
        mock_runner.run = AsyncMock(return_value=_make_run_result())

        first = await dream.run()
        second = await dream.run()

        assert first is True
        assert second is False
        assert mock_provider.chat_with_retry.await_count == 1
        assert mock_runner.run.await_count == 1

    async def test_phase2_uses_builtin_skill_creator_path(self, dream, mock_provider, mock_runner, store):
        _persist_committed_turn(store, session_key="cli:test", turn_id="turn_one", version=1)
        store.append_history("legacy audit entry")
        mock_provider.chat_with_retry.return_value = MagicMock(content="[SKILL] test-skill: test description")
        mock_runner.run = AsyncMock(return_value=_make_run_result())

        await dream.run()

        spec = mock_runner.run.call_args[0][0]
        system_prompt = spec.initial_messages[0]["content"]
        user_prompt = spec.initial_messages[1]["content"]
        expected = str(BUILTIN_SKILLS_DIR / "skill-creator" / "SKILL.md")
        assert expected in system_prompt
        assert "legacy audit entry" not in user_prompt
        assert "Mirror only" not in user_prompt

    async def test_dream_tools_cannot_write_identity_files(self, dream):
        edit_tool = dream._tools.get("edit_file")
        assert edit_tool is not None

        result = await edit_tool.execute(
            path="identity/SOUL.md",
            old_text="",
            new_text="# Soul\n- Mutated",
        )
        assert result.startswith("Error:")

    async def test_skill_write_tool_accepts_workspace_relative_skill_path(self, dream, store):
        write_tool = dream._tools.get("write_file")
        assert write_tool is not None

        result = await write_tool.execute(
            path="skills/test-skill/SKILL.md",
            content="---\nname: test-skill\ndescription: Test\n---\n",
        )

        assert "Successfully wrote" in result
        assert (store.workspace / "skills" / "test-skill" / "SKILL.md").exists()

