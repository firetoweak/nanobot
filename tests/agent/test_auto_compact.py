"""Tests for stage-6 auto compact behavior."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMResponse


def _make_loop(tmp_path: Path, session_ttl_minutes: int = 15) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.estimate_prompt_tokens.return_value = (10_000, "test")
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))
    provider.generation.max_tokens = 4096
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=128_000,
        session_ttl_minutes=session_ttl_minutes,
    )
    loop.tools.get_definitions = MagicMock(return_value=[])
    return loop


def _add_turns(session, turns: int, *, prefix: str = "msg") -> None:
    for i in range(turns):
        session.add_message("user", f"{prefix} user {i}")
        session.add_message("assistant", f"{prefix} assistant {i}")


def _commit_latest_turn(loop: AgentLoop, session_key: str) -> None:
    session = loop.sessions.get_or_create(session_key)
    turn_state = loop.create_turn_state(session_key)
    loop.finalize_turn(session, turn_state, final_content="done")
    loop.sessions.save(session)


class TestSessionTTLConfig:
    def test_default_ttl_is_zero(self):
        defaults = AgentDefaults()
        assert defaults.session_ttl_minutes == 0

    def test_custom_ttl(self):
        defaults = AgentDefaults(session_ttl_minutes=30)
        assert defaults.session_ttl_minutes == 30

    def test_user_friendly_alias_is_supported(self):
        defaults = AgentDefaults.model_validate({"idleCompactAfterMinutes": 30})
        assert defaults.session_ttl_minutes == 30

    def test_legacy_alias_is_still_supported(self):
        defaults = AgentDefaults.model_validate({"sessionTtlMinutes": 30})
        assert defaults.session_ttl_minutes == 30

    def test_serializes_with_user_friendly_alias(self):
        defaults = AgentDefaults(session_ttl_minutes=30)
        data = defaults.model_dump(mode="json", by_alias=True)
        assert data["idleCompactAfterMinutes"] == 30
        assert "sessionTtlMinutes" not in data


class TestAutoCompact:
    @pytest.mark.asyncio
    async def test_is_expired_boundary(self, tmp_path):
        loop = _make_loop(tmp_path, session_ttl_minutes=15)
        ts = datetime.now() - timedelta(minutes=15)
        assert loop.auto_compact._is_expired(ts) is True
        ts2 = datetime.now() - timedelta(minutes=14, seconds=59)
        assert loop.auto_compact._is_expired(ts2) is False
        assert loop.auto_compact._is_expired(None) is False
        await loop.close_mcp()

    @pytest.mark.asyncio
    async def test_archive_generates_candidate_snapshot_without_publishing_latest_working_set(self, tmp_path):
        loop = _make_loop(tmp_path)
        session = loop.sessions.get_or_create("cli:test")
        _add_turns(session, 6, prefix="old")
        loop.sessions.save(session)
        _commit_latest_turn(loop, "cli:test")

        latest_before = loop.sessions.load_latest_working_set("cli:test")
        await loop.auto_compact._archive("cli:test")

        session_after = loop.sessions.get_or_create("cli:test")
        candidate = loop.sessions.read_state_index("cli:test", "autocompact-candidate")
        latest_after = loop.sessions.load_latest_working_set("cli:test")

        assert len(session_after.messages) == loop.auto_compact._RECENT_SUFFIX_MESSAGES
        assert candidate is not None
        assert candidate["working_set_version"] == latest_before["version"] + 1
        assert latest_after["version"] == latest_before["version"]
        saved_candidate = loop.sessions.load_working_set("cli:test", candidate["working_set_version"])
        assert saved_candidate["published_by"] == "autocompact_candidate"
        assert saved_candidate["is_stable"] is False
        await loop.close_mcp()

    @pytest.mark.asyncio
    async def test_archive_requires_committed_turn(self, tmp_path):
        loop = _make_loop(tmp_path)
        session = loop.sessions.get_or_create("cli:test")
        _add_turns(session, 6)
        original_messages = list(session.messages)
        loop.sessions.save(session)

        await loop.auto_compact._archive("cli:test")

        session_after = loop.sessions.get_or_create("cli:test")
        candidate = loop.sessions.read_state_index("cli:test", "autocompact-candidate")
        assert session_after.messages == original_messages
        assert candidate is None
        await loop.close_mcp()

    @pytest.mark.asyncio
    async def test_archive_does_not_touch_active_turn_state(self, tmp_path):
        loop = _make_loop(tmp_path)
        session = loop.sessions.get_or_create("cli:test")
        _add_turns(session, 6)
        loop.sessions.save(session)
        active_turn = loop.create_turn_state("cli:test")

        await loop.auto_compact._archive("cli:test")

        session_after = loop.sessions.get_or_create("cli:test")
        active_after = loop.sessions.load_active_turn_state("cli:test")
        candidate = loop.sessions.read_state_index("cli:test", "autocompact-candidate")
        assert len(session_after.messages) == 12
        assert active_after == active_turn
        assert candidate is None
        await loop.close_mcp()

    @pytest.mark.asyncio
    async def test_prepare_session_returns_live_session_without_legacy_summary_path(self, tmp_path):
        loop = _make_loop(tmp_path)
        session = loop.sessions.get_or_create("cli:test")

        reloaded = loop.auto_compact.prepare_session(session, "cli:test")

        assert reloaded is session
        await loop.close_mcp()


class TestAutoCompactScheduling:
    @staticmethod
    async def _run_check_expired(loop, active_session_keys=()):
        loop.auto_compact.check_expired(loop._schedule_background, active_session_keys=active_session_keys)
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_check_expired_only_compacts_expired_sessions(self, tmp_path):
        loop = _make_loop(tmp_path)
        expired = loop.sessions.get_or_create("cli:expired")
        _add_turns(expired, 6, prefix="expired")
        expired.updated_at = datetime.now() - timedelta(minutes=20)
        loop.sessions.save(expired)
        _commit_latest_turn(loop, "cli:expired")

        active = loop.sessions.get_or_create("cli:active")
        _add_turns(active, 2, prefix="active")
        loop.sessions.save(active)
        _commit_latest_turn(loop, "cli:active")

        await self._run_check_expired(loop)

        expired_candidate = loop.sessions.read_state_index("cli:expired", "autocompact-candidate")
        active_candidate = loop.sessions.read_state_index("cli:active", "autocompact-candidate")
        assert expired_candidate is not None
        assert active_candidate is None
        await loop.close_mcp()

    @pytest.mark.asyncio
    async def test_skip_expired_session_with_active_agent_task(self, tmp_path):
        loop = _make_loop(tmp_path)
        session = loop.sessions.get_or_create("cli:test")
        _add_turns(session, 6)
        session.updated_at = datetime.now() - timedelta(minutes=20)
        loop.sessions.save(session)
        _commit_latest_turn(loop, "cli:test")

        await self._run_check_expired(loop, active_session_keys={"cli:test"})

        session_after = loop.sessions.get_or_create("cli:test")
        candidate = loop.sessions.read_state_index("cli:test", "autocompact-candidate")
        assert len(session_after.messages) == 12
        assert candidate is None
        await loop.close_mcp()
