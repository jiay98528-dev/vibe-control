#!/usr/bin/env python3
"""Narrow regression for claim-blocked pre-candidate milestone commits."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "assets" / "project-control" / "runtime"))

from vibe_runtime.automation_control import (  # noqa: E402
    failure_disposition,
    pre_candidate_milestone_side_effect_allowed,
)


def contract() -> dict:
    return {
        "guardPolicy": {
            "defaultEffect": "ADVISORY",
            "guards": [
                {"id": "GUARD-ACTION", "scope": "MUTATION", "effect": "ACTION_GUARD"},
                {"id": "GUARD-CLAIM", "scope": "CLAIM", "effect": "CLAIM_GUARD"},
                {"id": "GUARD-PROCESS", "scope": "PROCESS", "effect": "ADVISORY"},
                {"id": "GUARD-HUMAN", "scope": "HUMAN", "effect": "HUMAN_DECISION"},
                {"id": "GUARD-ENV", "scope": "ENVIRONMENT", "effect": "ENVIRONMENT_BLOCKED"},
            ],
        },
    }


def state(*, phase: str = "CONTRACT_LOCKED", health: str = "CLEAR") -> dict:
    return {"phase": phase, "health": health}


def blocked(check_id: str, status: str = "BLOCKED") -> dict:
    return {"id": check_id, "status": status, "message": "fixture"}


def expected_claim_blockers() -> list[dict]:
    return [
        blocked("HC-DEVELOPMENT-PACKAGE-CLAIM-CAP"),
        blocked("HC-ASSURANCE-MATRIX-INDEPENDENT"),
        blocked("HC-ASSURANCE-MATRIX-FORMAL"),
        blocked("HC-PROJECT-REVIEW-GATE"),
    ]


def test_expected_pre_candidate_claim_gap_allows_milestones() -> None:
    value = contract()
    commit_checks = [*expected_claim_blockers(), blocked("HC-WORKTREE-CLEAN")]
    commit_allowed = pre_candidate_milestone_side_effect_allowed(
        "commit", state(), None, commit_checks, "CLAIM_GUARD", value,
    )
    assert commit_allowed is True
    assert failure_disposition(
        value, "commit", "CLEAR", "CLAIM_GUARD",
        allow_claim_guarded_milestone=commit_allowed,
    ) == {
        "allowed": True,
        "repairRequired": True,
        "effect": "CLAIM_GUARD",
        "claimEligible": False,
    }
    push_allowed = pre_candidate_milestone_side_effect_allowed(
        "push", state(), None, expected_claim_blockers(), "CLAIM_GUARD", value,
    )
    assert push_allowed is True
    assert failure_disposition(
        value, "push", "CLEAR", "CLAIM_GUARD",
        allow_claim_guarded_milestone=push_allowed,
    ) == {
        "allowed": True,
        "repairRequired": True,
        "effect": "CLAIM_GUARD",
        "claimEligible": False,
    }
    assert pre_candidate_milestone_side_effect_allowed(
        "push", state(), None, commit_checks, "CLAIM_GUARD", value,
    ) is False


def test_candidate_failure_and_integrity_failure_stay_closed() -> None:
    value = contract()
    expected = [blocked("HC-PROJECT-REVIEW-GATE")]
    assert pre_candidate_milestone_side_effect_allowed(
        "commit", state(phase="CANDIDATE_FROZEN"), "candidate-1", expected, "CLAIM_GUARD", value,
    ) is False
    assert pre_candidate_milestone_side_effect_allowed(
        "commit", state(), None, [blocked("HC-RUNTIME-FILE-1", "FAIL")], "CLAIM_GUARD", value,
    ) is False
    assert pre_candidate_milestone_side_effect_allowed(
        "commit", state(health="FAILED"), None, expected, "CLAIM_GUARD", value,
    ) is False


def test_action_environment_human_and_unknown_blockers_stay_closed() -> None:
    value = contract()
    for effect in ("ACTION_GUARD", "ENVIRONMENT_BLOCKED", "HUMAN_DECISION"):
        assert pre_candidate_milestone_side_effect_allowed(
            "commit", state(), None, [blocked("HC-PROJECT-REVIEW-GATE")], effect, value,
        ) is False
        assert failure_disposition(value, "commit", "CLEAR", effect, allow_claim_guarded_milestone=True)["allowed"] is False
    assert pre_candidate_milestone_side_effect_allowed(
        "commit", state(), None, [blocked("HC-UNCLASSIFIED-BLOCKER")], "CLAIM_GUARD", value,
    ) is False
    assert pre_candidate_milestone_side_effect_allowed(
        "push", state(), None, [*expected_claim_blockers(), blocked("HC-CASE-COUNTERS")], "CLAIM_GUARD", value,
    ) is False


def main() -> int:
    tests = [
        test_expected_pre_candidate_claim_gap_allows_milestones,
        test_candidate_failure_and_integrity_failure_stay_closed,
        test_action_environment_human_and_unknown_blockers_stay_closed,
    ]
    results = []
    for test in tests:
        try:
            test()
            results.append({"test": test.__name__, "status": "PASS"})
        except Exception as exc:
            results.append({"test": test.__name__, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
    passed = sum(item["status"] == "PASS" for item in results)
    report = {
        "suite": "vibe-control-0.4.0-automation-claim-guard",
        "status": "PASS" if passed == len(results) else "FAIL",
        "counters": {"total": len(results), "passed": passed, "failed": len(results) - passed, "skipped": 0, "timedOut": 0},
        "tests": results,
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
