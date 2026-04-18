"""Tests for the layered MemoryStore file I/O contract."""

import json

import pytest

from nanobot.agent.memory import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


class TestMemoryStoreBasicIO:
    def test_state_dir_is_initialized_and_current_is_legacy_mirror(self, store):
        assert store.state_dir.exists()
        assert store.current_file == store.workspace / "working" / "CURRENT.md"
        assert store.read_current_mirror() == ""

    def test_read_identity_and_working_files_return_empty_when_missing(self, store):
        assert store.read_soul() == ""
        assert store.read_user_rules() == ""
        assert store.read_user_profile() == ""
        assert store.read_current() == ""

    def test_write_and_read_identity_and_working_files(self, store):
        store.write_soul("soul content")
        store.write_user_rules("rules content")
        store.write_user_profile("profile content")
        store.write_current("active task")

        assert store.read_soul() == "soul content"
        assert store.read_user_rules() == "rules content"
        assert store.read_user_profile() == "profile content"
        assert store.read_current() == "active task"

    def test_current_mirror_alias_matches_legacy_api(self, store):
        store.write_current_mirror("mirror content")

        assert store.read_current() == "mirror content"
        assert store.read_current_mirror() == "mirror content"

    def test_get_identity_context_only_uses_layered_identity_files(self, store):
        store.write_soul("# Soul\nStay calm.")
        store.write_user_rules("# Rules\nReply in Chinese.")
        store.write_user_profile("# Profile\nBackend developer.")

        ctx = store.get_identity_context()

        assert "identity/SOUL.md" in ctx
        assert "identity/USER_RULES.md" in ctx
        assert "identity/USER_PROFILE.md" in ctx
        assert "Stay calm." in ctx
        assert "Reply in Chinese." in ctx
        assert "Backend developer." in ctx

    def test_get_working_context_returns_formatted_content(self, store):
        store.write_current("# Current\nRefactoring memory.")

        ctx = store.get_working_context()

        assert ctx == "## working/CURRENT.md\n# Current\nRefactoring memory."

    def test_append_reflection(self, store):
        store.append_reflection("Summarized recent refactor")
        entries = store._read_jsonl(store.reflections_file)
        assert len(entries) == 1
        assert entries[0]["type"] == "archive_note"
        assert entries[0]["content"] == "Summarized recent refactor"

    def test_append_candidate_observation_uses_required_fields(self, store):
        record = store.append_candidate_observation({
            "type": "user_preference",
            "source": "explicit_user_statement",
            "confidence": 0.9,
            "evidence_count": 2,
            "status": "candidate",
            "promotion_target": "identity.USER_RULES",
            "content": "Reply in Chinese",
        })

        assert record["type"] == "user_preference"
        assert record["source"] == "explicit_user_statement"
        assert record["promotion_target"] == "identity.USER_RULES"
        assert store.read_candidate_observations()[0]["content"] == "Reply in Chinese"

    def test_ignores_removed_legacy_memory_files(self, tmp_path):
        (tmp_path / "SOUL.md").write_text("legacy soul", encoding="utf-8")
        (tmp_path / "USER.md").write_text("legacy user", encoding="utf-8")
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text("legacy memory", encoding="utf-8")
        (memory_dir / "HISTORY.md").write_text("[2026-04-01 10:00] legacy\n", encoding="utf-8")

        store = MemoryStore(tmp_path)

        assert store.read_soul() == ""
        assert store.read_user_rules() == ""
        assert store.read_user_profile() == ""
        assert store.read_unprocessed_history(since_cursor=0) == []


class TestHistoryWithCursor:
    def test_append_history_returns_cursor(self, store):
        cursor = store.append_history("event 1")
        cursor2 = store.append_history("event 2")

        assert cursor == 1
        assert cursor2 == 2

    def test_append_history_includes_cursor_in_file(self, store):
        store.append_history("event 1")

        content = store.read_file(store.history_file)
        data = json.loads(content)

        assert data["cursor"] == 1

    def test_cursor_persists_across_appends(self, store):
        store.append_history("event 1")
        store.append_history("event 2")

        cursor = store.append_history("event 3")

        assert cursor == 3

    def test_read_unprocessed_history(self, store):
        store.append_history("event 1")
        store.append_history("event 2")
        store.append_history("event 3")

        entries = store.read_unprocessed_history(since_cursor=1)

        assert len(entries) == 2
        assert entries[0]["cursor"] == 2

    def test_read_unprocessed_history_returns_all_when_cursor_zero(self, store):
        store.append_history("event 1")
        store.append_history("event 2")

        entries = store.read_unprocessed_history(since_cursor=0)

        assert len(entries) == 2

    def test_read_unprocessed_history_handles_entries_without_cursor(self, store):
        """JSONL entries with cursor=1 are correctly parsed and returned."""
        store.history_file.write_text(
            '{"cursor": 1, "timestamp": "2026-03-30 14:30", "content": "Old event"}\n',
            encoding="utf-8",
        )

        entries = store.read_unprocessed_history(since_cursor=0)

        assert len(entries) == 1
        assert entries[0]["cursor"] == 1

    def test_compact_history_drops_oldest(self, tmp_path):
        store = MemoryStore(tmp_path, max_history_entries=2)
        store.append_history("event 1")
        store.append_history("event 2")
        store.append_history("event 3")
        store.append_history("event 4")
        store.append_history("event 5")

        store.compact_history()
        entries = store.read_unprocessed_history(since_cursor=0)

        assert len(entries) == 2
        assert [entry["cursor"] for entry in entries] == [4, 5]


class TestDreamCursor:
    def test_initial_cursor_is_zero(self, store):
        assert store.get_last_dream_cursor() == 0

    def test_set_and_get_cursor(self, store):
        store.set_last_dream_cursor(5)
        assert store.get_last_dream_cursor() == 5

    def test_cursor_persists(self, store):
        store.set_last_dream_cursor(3)

        store2 = MemoryStore(store.workspace)

        assert store2.get_last_dream_cursor() == 3
