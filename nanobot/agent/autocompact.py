"""Auto compact: proactive compression of idle sessions to reduce token cost and latency."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from loguru import logger
from nanobot.session.manager import Session, SessionManager
from nanobot.session.state import COMMIT_STATE_COMMITTED, is_completed_manifest, timestamp as state_timestamp

if TYPE_CHECKING:
    from nanobot.agent.memory import Consolidator


class AutoCompact:
    _RECENT_SUFFIX_MESSAGES = 8

    def __init__(self, sessions: SessionManager, consolidator: Consolidator,
                 session_ttl_minutes: int = 0):
        self.sessions = sessions
        self.consolidator = consolidator
        self._ttl = session_ttl_minutes
        self._archiving: set[str] = set()

    def _is_expired(self, ts: datetime | str | None,
                    now: datetime | None = None) -> bool:
        if self._ttl <= 0 or not ts:
            return False
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return ((now or datetime.now()) - ts).total_seconds() >= self._ttl * 60

    def _split_unconsolidated(
        self, session: Session,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split live session tail into archiveable prefix and retained recent suffix."""
        tail = list(session.messages[session.last_consolidated:])
        if not tail:
            return [], []

        probe = Session(
            key=session.key,
            messages=tail.copy(),
            created_at=session.created_at,
            updated_at=session.updated_at,
            metadata={},
            last_consolidated=0,
        )
        probe.retain_recent_legal_suffix(self._RECENT_SUFFIX_MESSAGES)
        kept = probe.messages
        cut = len(tail) - len(kept)
        return tail[:cut], kept

    @staticmethod
    def _merge_refs(*groups: list[str] | None) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for group in groups:
            for ref in group or []:
                if not isinstance(ref, str) or not ref or ref in seen:
                    continue
                seen.add(ref)
                merged.append(ref)
        return merged

    def _load_latest_committed_objects(
        self,
        session_key: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None] | None:
        turn_state = self.sessions.load_latest_turn_state(session_key)
        if not isinstance(turn_state, dict):
            return None
        if turn_state.get("commit_state") != COMMIT_STATE_COMMITTED:
            return None
        commit_ref = turn_state.get("commit_manifest_ref")
        if not isinstance(commit_ref, str) or not commit_ref:
            return None
        manifest = self.sessions.resolve_ref(session_key, commit_ref)
        if not is_completed_manifest(manifest):
            return None
        if manifest.get("turn_revision") != turn_state.get("revision"):
            return None
        capsule_ref = manifest.get("capsule_ref")
        capsule = (
            self.sessions.resolve_ref(session_key, capsule_ref)
            if isinstance(capsule_ref, str) and capsule_ref
            else None
        )
        return turn_state, manifest, capsule if isinstance(capsule, dict) else None

    def _next_candidate_version(self, session_key: str) -> int:
        latest = self.sessions.load_latest_working_set(session_key)
        candidate = self.sessions.read_state_index(session_key, "autocompact-candidate")
        current_versions = [
            latest.get("version") if isinstance(latest, dict) else None,
            candidate.get("working_set_version") if isinstance(candidate, dict) else None,
        ]
        version = max((value for value in current_versions if isinstance(value, int)), default=0)
        return version + 1

    def _build_candidate_snapshot(
        self,
        *,
        session_key: str,
        turn_state: dict[str, Any],
        manifest: dict[str, Any],
        capsule: dict[str, Any] | None,
        version: int,
    ) -> dict[str, Any]:
        latest = self.sessions.load_latest_working_set(session_key)
        base = latest if isinstance(latest, dict) else {}
        capsule_ref = manifest.get("capsule_ref")
        last_user_focus = (
            base.get("last_user_focus")
            or (capsule or {}).get("user_goal")
            or None
        )
        active_goals = list(base.get("active_goals") or [])
        if not active_goals and isinstance(last_user_focus, str) and last_user_focus:
            active_goals = [last_user_focus]
        source_turn_ids = list(base.get("source_turn_ids") or [])
        turn_id = turn_state.get("turn_id")
        if isinstance(turn_id, str) and turn_id and turn_id not in source_turn_ids:
            source_turn_ids.append(turn_id)
        return {
            "session_key": session_key,
            "version": version,
            "source_turn_id": turn_id,
            "source_revision": manifest.get("turn_revision"),
            "is_stable": False,
            "published_by": "autocompact_candidate",
            "active_task": None,
            "task_stage": None,
            "active_goals": active_goals,
            "open_loops": list(base.get("open_loops") or []),
            "last_user_focus": last_user_focus,
            "relevant_capsule_refs": self._merge_refs(
                list(base.get("relevant_capsule_refs") or []),
                [capsule_ref] if isinstance(capsule_ref, str) and capsule_ref else [],
            ),
            "relevant_artifact_refs": self._merge_refs(
                list(base.get("relevant_artifact_refs") or []),
                list(manifest.get("artifact_refs") or []),
            ),
            "budget_hints": dict(base.get("budget_hints") or {}),
            "source_turn_ids": source_turn_ids,
            "created_at": state_timestamp(),
        }

    def _publish_candidate_index(
        self,
        *,
        session_key: str,
        snapshot: dict[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        self.sessions.save_working_set(session_key, snapshot)
        self.sessions.write_state_index(
            session_key,
            "autocompact-candidate",
            {
                "working_set_version": snapshot.get("version"),
                "source_turn_id": snapshot.get("source_turn_id"),
                "source_revision": snapshot.get("source_revision"),
                "capsule_ref": manifest.get("capsule_ref"),
                "artifact_refs": list(manifest.get("artifact_refs") or []),
                "created_at": snapshot.get("created_at"),
            },
        )

    def check_expired(self, schedule_background: Callable[[Coroutine], None],
                      active_session_keys: Collection[str] = ()) -> None:
        """Schedule archival for idle sessions, skipping those with in-flight agent tasks."""
        now = datetime.now()
        for info in self.sessions.list_sessions():
            key = info.get("key", "")
            if not key or key in self._archiving:
                continue
            if key in active_session_keys:
                continue
            if self._is_expired(info.get("updated_at"), now):
                self._archiving.add(key)
                schedule_background(self._archive(key))

    async def _archive(self, key: str) -> None:
        try:
            self.sessions.invalidate(key)
            session = self.sessions.get_or_create(key)
            if self.sessions.load_active_turn_state(key):
                session.updated_at = datetime.now()
                self.sessions.save(session)
                return
            committed = self._load_latest_committed_objects(key)
            if committed is None:
                session.updated_at = datetime.now()
                self.sessions.save(session)
                return
            turn_state, manifest, capsule = committed
            archive_msgs, kept_msgs = self._split_unconsolidated(session)
            if not archive_msgs and not kept_msgs:
                session.updated_at = datetime.now()
                self.sessions.save(session)
                return

            candidate_snapshot = self._build_candidate_snapshot(
                session_key=key,
                turn_state=turn_state,
                manifest=manifest,
                capsule=capsule,
                version=self._next_candidate_version(key),
            )
            self._publish_candidate_index(
                session_key=key,
                snapshot=candidate_snapshot,
                manifest=manifest,
            )
            session.messages = kept_msgs
            session.last_consolidated = 0
            session.updated_at = datetime.now()
            self.sessions.save(session)
            if archive_msgs:
                logger.info(
                    "Auto-compact: archived {} (archived={}, kept={}, candidate={})",
                    key,
                    len(archive_msgs),
                    len(kept_msgs),
                    candidate_snapshot.get("version"),
                )
        except Exception:
            logger.exception("Auto-compact: failed for {}", key)
        finally:
            self._archiving.discard(key)

    def prepare_session(self, session: Session, key: str) -> Session:
        if key in self._archiving or self._is_expired(session.updated_at):
            logger.info("Auto-compact: reloading session {} (archiving={})", key, key in self._archiving)
            session = self.sessions.get_or_create(key)
        return session
