from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.session.state import REF_ARTIFACT, REF_COMMIT, make_ref


def _make_full_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")


def test_finalize_turn_publishes_commit_objects_in_order(monkeypatch, tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:publish")
    session.add_message("assistant", "final answer")

    turn_state = loop.create_turn_state(session.key)
    artifact = {
        "artifact_id": "art_1",
        "session_key": session.key,
        "turn_id": turn_state["turn_id"],
        "tool_call_id": "call_1",
        "declared_revision": 0,
        "source_type": "read_file",
        "source_input": {"path": "note.txt"},
        "raw_ref": "raw.txt",
        "digest": "sha256:abc",
        "size_chars": 3,
        "freshness_policy": "file_bound",
        "content_version": "file:v1",
        "invalidated_by": [],
        "created_at": "2026-01-01T00:00:00",
        "prompt_render": "artifact",
    }
    loop.sessions.save_artifact(session.key, "art_1", artifact)
    turn_state = loop._advance_turn_state(
        turn_state,
        completed_tool_results=[
            {
                "tool_call_id": "call_1",
                "role": "tool",
                "name": "read_file",
                "content": "artifact",
                "artifact_ref": make_ref(REF_ARTIFACT, "art_1"),
            }
        ],
        artifact_refs=[make_ref(REF_ARTIFACT, "art_1")],
        current_stage="finalizing_turn",
    )

    operations: list[str] = []

    def _wrap(name: str) -> None:
        original = getattr(loop.sessions, name)

        def wrapper(*args, **kwargs):
            operations.append(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(loop.sessions, name, wrapper)

    for method_name in (
        "save_capsule",
        "save_working_set",
        "save_response_object",
        "save_commit_manifest",
        "save_turn_state",
        "publish_latest_turn",
        "publish_latest_working_set",
    ):
        _wrap(method_name)

    finalized = loop.finalize_turn(session, turn_state, final_content="final answer")

    assert finalized is not None
    assert finalized["current_stage"] == "completed"
    assert finalized["commit_state"] == "committed"
    assert operations.index("save_capsule") < operations.index("save_working_set")
    assert operations.index("save_working_set") < operations.index("save_response_object")
    assert operations.index("save_response_object") < operations.index("save_commit_manifest")
    assert operations.index("save_commit_manifest") < operations.index("save_turn_state")
    assert operations.index("save_turn_state") < operations.index("publish_latest_turn")
    assert operations.index("publish_latest_turn") < operations.index("publish_latest_working_set")

    latest = loop.sessions.load_latest_turn_state(session.key)
    assert latest is not None
    manifest = loop.sessions.resolve_ref(session.key, latest["commit_manifest_ref"])
    assert manifest is not None
    assert manifest["completed_marker"] is True
    assert loop.sessions.resolve_ref(session.key, manifest["final_response_ref"]) is not None
    assert loop.sessions.resolve_ref(session.key, manifest["capsule_ref"]) is not None
    assert loop.sessions.load_working_set(session.key, manifest["working_set_version"]) is not None
    assert loop.sessions.resolve_ref(session.key, make_ref(REF_COMMIT, manifest["commit_id"])) == manifest


def test_finalize_turn_advances_latest_working_set_only_after_stable_snapshot(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:stable")
    session.add_message("assistant", "done")
    turn_state = loop.create_turn_state(session.key)

    finalized = loop.finalize_turn(session, turn_state, final_content="done")

    assert finalized is not None
    latest_working_set = loop.sessions.load_latest_working_set(session.key)
    assert latest_working_set is not None
    assert latest_working_set["version"] == finalized["working_set_version"]
    assert latest_working_set["is_stable"] is True
