#!/usr/bin/env python3
"""Prove project formal paths from a genuinely sealed temporary Skill package."""

from __future__ import annotations

import json

import test_v2_support as fx


def private_operation_path() -> None:
    temp, root, keys = fx.setup_project(include_keys=False, release_intent="PRIVATE_OPERATION")
    try:
        fx.execute_and_verify(root)
        result, _ = fx.advance_audit(root, keys)
        if result.returncode != 0:
            raise AssertionError("private review failed")
        fx.commit(root, "private audit")
        result, _ = fx.advance_accept(root, keys)
        if result.returncode != 0:
            raise AssertionError("private owner decision failed")
        fx.commit(root, "private accept")
        _, report = fx.command(root, "release-check", expect=0)
        if report["formal"] != {"eligible": True, "maxClaimLevel": "ACCEPTED", "blockers": []}:
            raise AssertionError(f"private formal path did not close: {report['formal']}")
    finally:
        temp.cleanup()


def private_managed_decision_case_alias_path() -> None:
    temp, root, keys = fx.setup_project(include_keys=False, release_intent="PRIVATE_OPERATION")
    try:
        fx.execute_and_verify(root)
        result, _ = fx.advance_audit(root, keys)
        if result.returncode != 0:
            raise AssertionError("private review failed")
        fx.commit(root, "private audit")
        candidate = fx.load(next((root / ".vibe-control" / "candidates").glob("*.json")))
        decision = {
            "schemaVersion": "3.2", "decisionId": "DECISION-CASE", "taskId": "TASK-001",
            "candidateId": candidate["candidateId"], "candidateCommit": candidate["commit"],
            "checkpointSetSha256": candidate["checkpointSetSha256"],
            "checkpointDecisions": [{"checkpointId": "CP-002", "decision": "PASS"}],
            "positioning": candidate["positioning"], "resolvedRuleSet": candidate["resolvedRuleSet"],
            "scope": candidate["changedPaths"], "owner": {"actorId": "owner"}, "decision": "APPROVE",
            "decidedAt": "2026-07-26T00:00:00+00:00", "expiresAt": None,
        }
        source = root / ".vibe-control" / "decisions" / "decision-case.json"
        fx.write(source, decision)
        fx.commit(root, "track lower-case decision input")
        result, _ = fx.command(root, "accept", "--decision", str(source), expect=None)
        if result.returncode != 0:
            raise AssertionError("managed decision input was rejected")
        fx.commit(root, "accept managed decision")
        _, report = fx.command(root, "validate", expect=0)
        if report["formal"]["eligible"] is not True or "HC-DECISION-TRACKED" in fx.failing_ids(report):
            raise AssertionError(f"managed decision path lost identity: {report['formal']}")
    finally:
        temp.cleanup()


def external_release_path() -> None:
    import test_v2_security as security

    temp, root, keys = security.setup_r3_release_project()
    try:
        result, _ = fx.advance_audit(root, keys)
        if result.returncode != 0:
            raise AssertionError("external review failed")
        fx.commit(root, "external audit")
        result, _ = fx.advance_accept(root, keys)
        if result.returncode != 0:
            raise AssertionError("external owner decision failed")
        fx.commit(root, "external accept")
        fx.install_release_chain(root, keys)
        _, transition = fx.command(root, "release-check", expect=2)
        if transition["state"]["declared"]["phase"] != "RELEASE_READY":
            raise AssertionError("release-check did not perform the controlled transition")
        fx.commit(root, "advance release state")
        _, report = fx.command(root, "release-check", expect=0)
        if report["formal"]["eligible"] is not True or report["formal"]["maxClaimLevel"] != "RELEASE_READY":
            raise AssertionError(f"external formal path did not close: {report['formal']}")
    finally:
        temp.cleanup()


def test_formal_activation_paths() -> dict:
    results = []
    for name, test in [
        ("private_operation", private_operation_path),
        ("private_managed_decision_case_alias", private_managed_decision_case_alias_path),
        ("external_release", external_release_path),
    ]:
        try:
            test()
            results.append({"case": name, "status": "PASS"})
        except Exception as exc:
            results.append({"case": name, "status": "FAIL", "error": str(exc)})
    passed = sum(item["status"] == "PASS" for item in results)
    report = {"status": "PASS" if passed == len(results) else "FAIL", "counters": {"total": len(results), "passed": passed, "failed": len(results) - passed}, "cases": results}
    if report["status"] != "PASS":
        raise AssertionError(report)
    return report


def main() -> int:
    try:
        report = test_formal_activation_paths()
    except Exception as exc:
        print(json.dumps({"test": "test_formal_activation_paths", "status": "FAIL", "counters": {"total": 3, "passed": 0, "failed": 1}, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"test": "test_formal_activation_paths", **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
