"""Tests for stage-6 Consolidator behavior."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.memory import Consolidator, MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock()
    return provider


@pytest.fixture
def sessions():
    value = MagicMock()
    value.save = MagicMock()
    value.load_latest_working_set = MagicMock(
        return_value={"version": 3, "last_user_focus": "Refactor memory"}
    )
    return value


@pytest.fixture
def build_messages():
    return MagicMock(return_value=[{"role": "system", "content": "probe"}])


@pytest.fixture
def consolidator(store, mock_provider, sessions, build_messages):
    return Consolidator(
        store=store,
        provider=mock_provider,
        model="test-model",
        sessions=sessions,
        context_window_tokens=1000,
        build_messages=build_messages,
        get_tool_definitions=MagicMock(return_value=[]),
        max_completion_tokens=100,
    )


class TestConsolidatorSummarize:
    async def test_summarize_appends_to_history(self, consolidator, mock_provider, store):
        mock_provider.chat_with_retry.return_value = MagicMock(
            content="User fixed a bug in the auth module."
        )
        result = await consolidator.archive(
            [
                {"role": "user", "content": "fix the auth bug"},
                {"role": "assistant", "content": "Done, fixed the race condition."},
            ]
        )
        assert result == "User fixed a bug in the auth module."
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries) == 1

    async def test_summarize_raw_dumps_on_llm_failure(self, consolidator, mock_provider, store):
        mock_provider.chat_with_retry.side_effect = Exception("API error")
        result = await consolidator.archive([{"role": "user", "content": "hello"}])
        assert result is None
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries) == 1
        assert "[RAW]" in entries[0]["content"]


class TestConsolidatorTokenBudget:
    def test_token_probe_uses_structured_build_messages(self, consolidator, build_messages, sessions):
        session = MagicMock()
        session.key = "cli:test"
        session.get_history.return_value = [{"role": "user", "content": "hi"}]

        with patch("nanobot.agent.memory.estimate_prompt_tokens_chain", return_value=(321, "probe")):
            estimated, source = consolidator.estimate_session_prompt_tokens(session)

        assert estimated == 321
        assert source == "probe"
        sessions.load_latest_working_set.assert_called_once_with("cli:test")
        kwargs = build_messages.call_args.kwargs
        assert kwargs["working_set"]["version"] == 3
        assert kwargs["recent_raw_turns"] == [{"role": "user", "content": "hi"}]
        assert kwargs["selected_capsules"] == []
        assert kwargs["selected_artifacts"] == []
        assert "history" not in kwargs

    async def test_prompt_below_threshold_does_not_consolidate(self, consolidator):
        session = MagicMock()
        session.last_consolidated = 0
        session.messages = [{"role": "user", "content": "hi"}]
        session.key = "test:key"
        consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(100, "tiktoken"))
        consolidator.archive = AsyncMock(return_value=True)
        await consolidator.maybe_consolidate_by_tokens(session)
        consolidator.archive.assert_not_called()

    async def test_chunk_cap_preserves_user_turn_boundary(self, consolidator):
        consolidator._SAFETY_BUFFER = 0
        session = MagicMock()
        session.last_consolidated = 0
        session.key = "test:key"
        session.messages = [
            {"role": "user" if i in {0, 50, 61} else "assistant", "content": f"m{i}"}
            for i in range(70)
        ]
        consolidator.estimate_session_prompt_tokens = MagicMock(
            side_effect=[(1200, "tiktoken"), (400, "tiktoken")]
        )
        consolidator.pick_consolidation_boundary = MagicMock(return_value=(61, 999))
        consolidator.archive = AsyncMock(return_value=True)

        await consolidator.maybe_consolidate_by_tokens(session)

        archived_chunk = consolidator.archive.await_args.args[0]
        assert len(archived_chunk) == 50
        assert archived_chunk[0]["content"] == "m0"
        assert archived_chunk[-1]["content"] == "m49"
        assert session.last_consolidated == 50
