from nanobot.agent.memory import MemoryStore
from nanobot.agent.promoter import Promoter


def test_promotes_explicit_user_statement_into_rules(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.append_candidate_observation({
        "type": "user_preference",
        "source": "explicit_user_statement",
        "confidence": 0.95,
        "evidence_count": 1,
        "status": "candidate",
        "promotion_target": "identity.USER_RULES",
        "content": "Default to Chinese responses",
    })

    changed = Promoter(store).run()

    assert changed is True
    assert "Default to Chinese responses" in store.read_user_rules()
    assert store.read_candidate_observations()[0]["status"] == "promoted"


def test_promotes_repeated_observation_into_profile(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.append_candidate_observation({
        "type": "user_profile",
        "source": "dream_inference",
        "confidence": 0.8,
        "evidence_count": 3,
        "status": "candidate",
        "promotion_target": "identity.USER_PROFILE",
        "content": "Works on NanoBot memory architecture",
    })

    changed = Promoter(store, repeat_threshold=2).run()

    assert changed is True
    assert "Works on NanoBot memory architecture" in store.read_user_profile()
    assert store.read_candidate_observations()[0]["status"] == "promoted"


def test_rejects_low_confidence_candidate(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.append_candidate_observation({
        "type": "user_preference",
        "source": "dream_inference",
        "confidence": 0.1,
        "evidence_count": 1,
        "status": "candidate",
        "promotion_target": "identity.USER_RULES",
        "content": "Likes very long answers",
    })

    changed = Promoter(store).run()

    assert changed is True
    assert store.read_candidate_observations()[0]["status"] == "rejected"
