"""Tests for cache-friendly prompt construction."""

from __future__ import annotations

from datetime import datetime as real_datetime
from importlib.resources import files as pkg_files
from pathlib import Path
import datetime as datetime_module

from nanobot.agent.context import ContextBuilder


class _FakeDatetime(real_datetime):
    current = real_datetime(2026, 2, 24, 13, 59)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.current


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def test_bootstrap_files_are_backed_by_templates() -> None:
    template_dir = pkg_files("nanobot") / "templates"

    for filename in ContextBuilder.BOOTSTRAP_FILES:
        assert (template_dir / filename).is_file(), f"missing bootstrap template: {filename}"


def test_system_prompt_stays_stable_when_clock_changes(tmp_path, monkeypatch) -> None:
    """System prompt should not change just because wall clock minute changes."""
    monkeypatch.setattr(datetime_module, "datetime", _FakeDatetime)

    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    _FakeDatetime.current = real_datetime(2026, 2, 24, 13, 59)
    prompt1 = builder.build_system_prompt()

    _FakeDatetime.current = real_datetime(2026, 2, 24, 14, 0)
    prompt2 = builder.build_system_prompt()

    assert prompt1 == prompt2


def test_system_prompt_reflects_current_dream_memory_contract(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()

    assert "identity/" in prompt
    assert "working/CURRENT.md" in prompt
    assert "archive/history.jsonl" in prompt
    assert "candidate/observations.jsonl" in prompt
    assert "memory/history.jsonl" not in prompt
    assert "memory/MEMORY.md" not in prompt


def test_runtime_context_is_separate_untrusted_user_message(tmp_path) -> None:
    """Runtime metadata should be merged with the user message."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="Return exactly: OK",
        channel="cli",
        chat_id="direct",
    )

    assert messages[0]["role"] == "system"
    assert "## Current Session" not in messages[0]["content"]

    # Runtime context is now merged with user message into a single message
    assert messages[-1]["role"] == "user"
    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert ContextBuilder._RUNTIME_CONTEXT_TAG in user_content
    assert "Current Time:" in user_content
    assert "Channel: cli" in user_content
    assert "Chat ID: direct" in user_content
    assert "Return exactly: OK" in user_content


def test_archive_history_is_not_injected_into_system_prompt(tmp_path) -> None:
    """Archive history should stay searchable, not resident in the system prompt."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    builder.memory.append_history("User asked about weather in Tokyo")
    builder.memory.append_history("Agent fetched forecast via web_search")

    prompt = builder.build_system_prompt()
    assert "# Recent History" not in prompt
    assert "User asked about weather in Tokyo" not in prompt
    assert "Agent fetched forecast via web_search" not in prompt


def test_identity_and_working_memory_are_injected(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    from nanobot.utils.helpers import sync_workspace_templates

    sync_workspace_templates(workspace, silent=True)
    (workspace / "identity" / "SOUL.md").write_text("# Soul\n\nStay calm.", encoding="utf-8")
    (workspace / "identity" / "USER_RULES.md").write_text("# User Rules\n\n- Reply in Chinese.", encoding="utf-8")
    (workspace / "working" / "CURRENT.md").write_text("# Current\n\n- Refactoring memory.", encoding="utf-8")

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()

    assert "# Identity Memory" in prompt
    assert "Stay calm." in prompt
    assert "Reply in Chinese." in prompt
    assert "# Working Memory" in prompt
    assert "Refactoring memory." in prompt


def test_execution_rules_in_system_prompt(tmp_path) -> None:
    """New execution rules should appear in the system prompt."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()
    assert "Act, don't narrate" in prompt
    assert "Read before you write" in prompt
    assert "verify the result" in prompt


def test_channel_format_hint_telegram(tmp_path) -> None:
    """Telegram channel should get messaging-app format hint."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt(channel="telegram")
    assert "Format Hint" in prompt
    assert "messaging app" in prompt


def test_channel_format_hint_whatsapp(tmp_path) -> None:
    """WhatsApp should get plain-text format hint."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt(channel="whatsapp")
    assert "Format Hint" in prompt
    assert "plain text only" in prompt


def test_channel_format_hint_absent_for_unknown(tmp_path) -> None:
    """Unknown or None channel should not inject a format hint."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt(channel=None)
    assert "Format Hint" not in prompt

    prompt2 = builder.build_system_prompt(channel="feishu")
    assert "Format Hint" not in prompt2


def test_build_messages_passes_channel_to_system_prompt(tmp_path) -> None:
    """build_messages should pass channel through to build_system_prompt."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[], current_message="hi",
        channel="telegram", chat_id="123",
    )
    system = messages[0]["content"]
    assert "Format Hint" in system
    assert "messaging app" in system


def test_subagent_result_does_not_create_consecutive_assistant_messages(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[{"role": "assistant", "content": "previous result"}],
        current_message="subagent result",
        channel="cli",
        chat_id="direct",
        current_role="assistant",
    )

    for left, right in zip(messages, messages[1:]):
        assert not (left.get("role") == right.get("role") == "assistant")


def test_always_skills_excluded_from_skills_index(tmp_path) -> None:
    """Always skills should appear in Active Skills but NOT in the skills index."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()

    # memory skill should be in Active Skills section
    assert "# Active Skills" in prompt
    assert "### Skill: memory" in prompt

    # memory skill should NOT appear in the skills index
    skills_section = prompt.split("# Skills\n", 1)
    if len(skills_section) > 1:
        index_text = skills_section[1].split("\n\n---")[0]
        assert "**memory**" not in index_text


def test_removed_legacy_memory_files_do_not_affect_system_prompt(tmp_path) -> None:
    """Removed legacy memory files should not affect the system prompt."""
    workspace = _make_workspace(tmp_path)
    from nanobot.utils.helpers import sync_workspace_templates
    sync_workspace_templates(workspace, silent=True)

    (workspace / "SOUL.md").write_text("# Soul\n\nLegacy soul.\n", encoding="utf-8")
    (workspace / "USER.md").write_text("# User\n\nLegacy user.\n", encoding="utf-8")
    (workspace / "memory").mkdir()
    (workspace / "memory" / "MEMORY.md").write_text(
        "# Long-term Memory\n\nUser prefers dark mode.\n", encoding="utf-8"
    )
    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()

    assert "SOUL.md" in prompt  # layered identity path listing still exists
    assert "memory/MEMORY.md" not in prompt
    assert "User prefers dark mode" not in prompt
    assert "Legacy soul." not in prompt
    assert "Legacy user." not in prompt
