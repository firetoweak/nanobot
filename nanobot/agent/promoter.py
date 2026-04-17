"""Promote candidate observations into stable identity files."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore


class Promoter:
    """Apply simple hard rules to promote candidate observations."""

    def __init__(self, store: MemoryStore, repeat_threshold: int = 2):
        self.store = store
        self.repeat_threshold = repeat_threshold

    def _target_path(self, observation: dict[str, Any]) -> Path | None:
        target = str(observation.get("promotion_target") or "")
        mapping = {
            "identity.USER_RULES": self.store.user_rules_file,
            "identity.USER_PROFILE": self.store.user_profile_file,
            "identity.SOUL": self.store.soul_file,
        }
        return mapping.get(target)

    @staticmethod
    def _normalize_content(content: str) -> str:
        return content.strip().lstrip("-").strip()

    def _should_promote(self, observation: dict[str, Any]) -> tuple[bool, str]:
        if observation.get("status") not in {"candidate", "observed", "promotion_proposal"}:
            return False, ""
        if not self._normalize_content(str(observation.get("content") or "")):
            return False, ""
        if observation.get("source") == "explicit_user_statement":
            return True, "explicit_user_statement"
        if int(observation.get("evidence_count") or 0) >= self.repeat_threshold:
            return True, "repeated_evidence"
        return False, ""

    @staticmethod
    def _should_reject(observation: dict[str, Any]) -> tuple[bool, str]:
        if observation.get("contradicted_by"):
            return True, "contradicted"
        if float(observation.get("confidence") or 0) < 0.25:
            return True, "low_confidence"
        return False, ""

    def _append_identity_fact(self, path: Path, content: str) -> bool:
        normalized = self._normalize_content(content)
        if not normalized:
            return False
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if normalized in existing:
            return False

        bullet = f"- {normalized}\n"
        if existing.strip():
            updated = existing.rstrip() + "\n\n" + bullet
        else:
            title = {
                self.store.user_rules_file: "# User Rules\n\n",
                self.store.user_profile_file: "# User Profile\n\n",
                self.store.soul_file: "# Soul\n\n",
            }.get(path, "")
            updated = title + bullet
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated, encoding="utf-8")
        return True

    def run(self) -> bool:
        observations = self.store.read_candidate_observations()
        if not observations:
            return False

        changed = False
        now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        for observation in observations:
            reject, reject_reason = self._should_reject(observation)
            if reject:
                if observation.get("status") != "rejected":
                    observation["status"] = "rejected"
                    observation["rejected_at"] = now
                    observation["resolution_reason"] = reject_reason
                    changed = True
                continue

            promote, promote_reason = self._should_promote(observation)
            if not promote:
                continue

            target = self._target_path(observation)
            if target is None:
                continue
            file_changed = self._append_identity_fact(target, str(observation.get("content") or ""))
            if observation.get("status") != "promoted" or file_changed:
                observation["status"] = "promoted"
                observation["promoted_at"] = now
                observation["resolution_reason"] = promote_reason
                changed = True

        if changed:
            self.store.write_candidate_observations(observations)
        return changed
