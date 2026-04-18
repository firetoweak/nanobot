from __future__ import annotations

from nanobot.agent.artifact_render import artifact_invalidation_reasons, persist_tool_artifact


def test_read_file_artifact_invalidates_when_file_changes(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("v1", encoding="utf-8")

    artifact = persist_tool_artifact(
        workspace=tmp_path,
        session_key="cli:test",
        turn_id="turn_1",
        artifact_id="artifact_file_1",
        tool_call_id="call_1",
        source_type="read_file",
        source_input={"path": str(target)},
        payload="v1",
        declared_revision=1,
    )

    assert artifact_invalidation_reasons(artifact, workspace=tmp_path) == []

    target.write_text("v2", encoding="utf-8")

    assert "source_changed" in artifact_invalidation_reasons(artifact, workspace=tmp_path)


def test_exec_artifact_invalidates_when_command_signature_changes(tmp_path):
    artifact = persist_tool_artifact(
        workspace=tmp_path,
        session_key="cli:test",
        turn_id="turn_1",
        artifact_id="artifact_exec_1",
        tool_call_id="call_1",
        source_type="exec",
        source_input={"cmd": "git status", "cwd": str(tmp_path)},
        payload="ok",
        declared_revision=1,
    )

    assert artifact_invalidation_reasons(artifact, workspace=tmp_path) == []

    reasons = artifact_invalidation_reasons(
        artifact,
        workspace=tmp_path,
        current_source_input={"cmd": "git diff", "cwd": str(tmp_path)},
    )

    assert "command_signature_changed" in reasons
