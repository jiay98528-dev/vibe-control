"""Deterministic checkpoint-contract helpers for Schema 3.2."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any

from .common import ControlError, canonical_bytes, check, sha256_bytes


CLAIMS = ("DIAGNOSTIC", "DEVELOPMENT_CHECKED", "VERIFIED", "ACCEPTED", "RELEASE_READY")
AUDIT_POLICY = {
    "mode": "CONFORMANCE_PLUS_BOUNDED_EXPLORATION",
    "maxExploratoryFindings": 3,
    "stopCondition": "ALL_REQUIRED_CHECKPOINTS_REPORTED",
}
EXPLORATORY_FINDING_CLASSES = {
    "PROCESS_WARNING", "INVESTIGATION", "FUTURE_PROPOSAL", "OUT_OF_SCOPE",
}
MINIMUM_CORE_CONTROL_IDS = {
    "RULE-CORE-OBSERVABLE-CANDIDATE",
    "RULE-CORE-FAILURE-CONSERVATION",
}
_SPACE = re.compile(r"\s+")


def normalize_statement(value: str) -> str:
    if not isinstance(value, str):
        raise ControlError("HC-CHECKPOINT-SOURCE-ID", "signal/gate statement must be a string")
    return _SPACE.sub(" ", unicodedata.normalize("NFC", value).strip())


def statement_id(prefix: str, statement: str) -> str:
    normalized = normalize_statement(statement)
    if not normalized:
        raise ControlError("HC-CHECKPOINT-SOURCE-ID", "signal/gate statement cannot be empty")
    return f"{prefix}-{sha256_bytes(normalized.encode('utf-8'))[:12]}"


def validate_statement_objects(items: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    """Validate content-derived IDs and reject duplicate text or hash collisions."""
    normalized: dict[str, str] = {}
    ids: dict[str, str] = {}
    checks: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        text = normalize_statement(item.get("statement", ""))
        expected = statement_id(prefix, text)
        actual = item.get("id")
        if text in normalized:
            raise ControlError(
                "HC-CHECKPOINT-SOURCE-DUPLICATE",
                f"duplicate normalized {prefix} statement",
                details={"firstId": normalized[text], "duplicateIndex": index},
            )
        if actual in ids and ids[actual] != text:
            raise ControlError(
                "HC-CHECKPOINT-SOURCE-COLLISION",
                f"{prefix} ID collision",
                details={"id": actual},
            )
        if actual != expected:
            raise ControlError(
                "HC-CHECKPOINT-SOURCE-ID",
                f"{prefix} ID is not derived from the normalized statement",
                details={"actual": actual, "expected": expected},
            )
        normalized[text] = actual
        ids[actual] = text
    checks.append(check(
        "HC-CHECKPOINT-SOURCE-ID", "PASS",
        f"{prefix} statements have unique content-derived IDs",
        count=len(items),
    ))
    return checks


def positioning_checkpoint_source_checks(positioning: dict[str, Any]) -> list[dict[str, Any]]:
    checks = validate_statement_objects(positioning["firstVerticalSlice"]["successSignals"], "SIG")
    checks.extend(validate_statement_objects(positioning["humanQualityGates"], "HG"))
    return checks


def checkpoint_set_payload(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "acceptanceCheckpoints": contract["acceptanceCheckpoints"],
        "auditPolicy": contract["auditPolicy"],
    }


def checkpoint_set_sha256(contract: dict[str, Any]) -> str:
    return sha256_bytes(canonical_bytes(checkpoint_set_payload(contract)))


def checkpoint_by_id(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in contract["acceptanceCheckpoints"]}


def checkpoint_ids_for_case(contract: dict[str, Any], case_id: str) -> list[str]:
    return sorted(item["id"] for item in contract["acceptanceCheckpoints"] if case_id in item["caseIds"])


def evaluate_case_oracle(
    case: dict[str, Any], *, exit_code: int, stdout: str, stderr: str,
    artifact_sizes: dict[str, int | None],
) -> tuple[bool, dict[str, Any]]:
    """Evaluate only the locked, typed oracle; never interpret free-text quality."""
    oracle = case["oracle"]
    missing_stdout = [value for value in oracle["stdoutContainsAll"] if value not in stdout]
    forbidden_stderr = [value for value in oracle["stderrContainsNone"] if value in stderr]
    artifact_failures = [
        {
            "path": requirement["path"],
            "requiredMinBytes": requirement["minBytes"],
            "observedBytes": artifact_sizes.get(requirement["path"]),
        }
        for requirement in case.get("artifacts", [])
        if artifact_sizes.get(requirement["path"]) is None
        or artifact_sizes[requirement["path"]] < requirement["minBytes"]
    ]
    details = {
        "expectedExitCode": oracle["exitCode"],
        "observedExitCode": exit_code,
        "missingStdout": missing_stdout,
        "forbiddenStderr": forbidden_stderr,
        "artifactFailures": artifact_failures,
    }
    return (
        exit_code == oracle["exitCode"]
        and not missing_stdout
        and not forbidden_stderr
        and not artifact_failures
    ), details


def _upward_closed(claims: list[str]) -> bool:
    if not claims:
        return False
    indexes = sorted(CLAIMS.index(value) for value in claims)
    return indexes == list(range(indexes[0], len(CLAIMS)))


def claim_is_affected(finding: dict[str, Any], claim: str) -> bool:
    return finding.get("status") == "OPEN" and claim in finding.get("affectedClaims", [])


def checkpoint_contract_checks(
    contract: dict[str, Any], positioning: dict[str, Any], catalog: dict[str, Any], release_intent_cap: str,
) -> list[dict[str, Any]]:
    """Check cross-object closure that JSON Schema cannot express."""
    checkpoints = contract["acceptanceCheckpoints"]
    checkpoint_ids = [item["id"] for item in checkpoints]
    if len(checkpoint_ids) != len(set(checkpoint_ids)):
        raise ControlError("HC-CHECKPOINT-DUPLICATE", "checkpoint IDs must be unique")
    if contract["auditPolicy"] != AUDIT_POLICY:
        raise ControlError("HC-AUDIT-STOP-CLOSURE", "task auditPolicy must equal the fixed bounded policy")

    signal_ids = {item["id"] for item in positioning["firstVerticalSlice"]["successSignals"]}
    gate_ids = {item["id"] for item in positioning["humanQualityGates"]}
    known_sources = signal_ids | gate_ids
    known_cases = {item["id"]: item for item in catalog["cases"]}
    task_cases = set(contract["requiredCaseIds"])
    task_objectives = set(contract["objectiveRefs"])
    source_counts: Counter[str] = Counter()
    case_counts: Counter[str] = Counter()
    assertion_ids: set[str] = set()

    for checkpoint_item in checkpoints:
        unknown_sources = sorted(set(checkpoint_item["sourceRefs"]) - known_sources)
        if unknown_sources:
            raise ControlError("HC-CHECKPOINT-SOURCE-CLOSURE", "checkpoint references unknown positioning sources", details=unknown_sources)
        unknown_objectives = sorted(set(checkpoint_item["objectiveRefs"]) - task_objectives)
        if unknown_objectives:
            raise ControlError("HC-CHECKPOINT-OBJECTIVE-CLOSURE", "checkpoint objectives exceed current task objectives", details=unknown_objectives)
        unknown_cases = sorted(set(checkpoint_item["caseIds"]) - task_cases)
        if unknown_cases:
            raise ControlError("HC-CHECKPOINT-CASE-CLOSURE", "checkpoint cases exceed the current task", details=unknown_cases)
        for source_ref in checkpoint_item["sourceRefs"]:
            source_counts[source_ref] += 1
        for case_id in checkpoint_item["caseIds"]:
            case_counts[case_id] += 1

        if checkpoint_item["type"] == "AUTOMATED":
            if not checkpoint_item["caseIds"] or not checkpoint_item["assertions"]:
                raise ControlError("HC-CHECKPOINT-AUTOMATED-CLOSURE", "automated checkpoints require cases and assertions")
            for assertion in checkpoint_item["assertions"]:
                if assertion["id"] in assertion_ids:
                    raise ControlError("HC-CHECKPOINT-ASSERTION-DUPLICATE", "assertion IDs must be unique across the task")
                assertion_ids.add(assertion["id"])
                if not assertion["caseIds"] or not set(assertion["caseIds"]).issubset(set(checkpoint_item["caseIds"])):
                    raise ControlError("HC-CHECKPOINT-ASSERTION-CLOSURE", "assertion cases must be a nonempty subset of its checkpoint cases")
        elif not (set(checkpoint_item["sourceRefs"]) & gate_ids):
            raise ControlError("HC-CHECKPOINT-HUMAN-CLOSURE", "human checkpoints must reference a locked HG source")

        claim_indexes = [CLAIMS.index(contract["maxClaimLevel"]), CLAIMS.index(release_intent_cap)]
        claim_indexes.extend(CLAIMS.index(known_cases[case_id]["maxClaimLevel"]) for case_id in checkpoint_item["caseIds"])
        ceiling = min(claim_indexes)
        if CLAIMS.index(checkpoint_item["requiredForClaim"]) > ceiling:
            raise ControlError("HC-CHECKPOINT-CLAIM-CEILING", "checkpoint claim exceeds task, release-intent, or case ceiling", details={"checkpointId": checkpoint_item["id"], "ceiling": CLAIMS[ceiling]})

    bad_signals = {item: source_counts[item] for item in signal_ids if source_counts[item] != 1}
    if bad_signals:
        raise ControlError("HC-CHECKPOINT-SIGNAL-CLOSURE", "every success signal must map exactly once", details=bad_signals)
    if CLAIMS.index(contract["maxClaimLevel"]) >= CLAIMS.index("ACCEPTED"):
        bad_gates = {item: source_counts[item] for item in gate_ids if source_counts[item] != 1}
        if bad_gates:
            raise ControlError("HC-CHECKPOINT-HUMAN-CLOSURE", "every human gate must map exactly once for an ACCEPTED-capable task", details=bad_gates)
    duplicated_gates = {item: source_counts[item] for item in gate_ids if source_counts[item] > 1}
    if duplicated_gates:
        raise ControlError("HC-CHECKPOINT-HUMAN-CLOSURE", "human gates cannot map more than once", details=duplicated_gates)
    missing_cases = sorted(case_id for case_id in task_cases if case_counts[case_id] < 1)
    if missing_cases:
        raise ControlError("HC-CHECKPOINT-CASE-CLOSURE", "every required case must map to at least one checkpoint", details=missing_cases)

    expected_hash = checkpoint_set_sha256(contract)
    confirmation = contract["checkpointConfirmation"]
    if confirmation["checkpointSetSha256"] != expected_hash:
        raise ControlError("HC-CHECKPOINT-CONFIRMATION", "checkpoint confirmation does not bind the normalized checkpoint set", details={"expected": expected_hash, "actual": confirmation["checkpointSetSha256"]})
    return [
        check("HC-CHECKPOINT-SIGNAL-CLOSURE", "PASS", "every task success signal maps exactly once"),
        check("HC-CHECKPOINT-CASE-CLOSURE", "PASS", "every required case maps to a checkpoint"),
        check("HC-CHECKPOINT-CONFIRMATION", "PASS", "one confirmation binds the checkpoint set", checkpointSetSha256=expected_hash),
        check("HC-AUDIT-STOP-CLOSURE", "PASS", "task uses the fixed bounded audit policy"),
    ]


def derive_checkpoint_result(checkpoint_item: dict[str, Any], evidence_by_case: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    missing = [case_id for case_id in checkpoint_item["caseIds"] if case_id not in evidence_by_case]
    evidence = [evidence_by_case[case_id] for case_id in checkpoint_item["caseIds"] if case_id in evidence_by_case]
    if missing:
        return "BLOCKED", sorted(item["evidenceId"] for item in evidence)
    expected = checkpoint_item["expected"]
    executed = sum(item["counters"]["executed"] for item in evidence)
    failed = sum(item["counters"]["failed"] for item in evidence)
    skipped = sum(item["counters"]["skipped"] for item in evidence)
    passed = (
        bool(evidence)
        and all(item["result"] == expected["status"] for item in evidence)
        and executed >= expected["minExecuted"]
        and failed <= expected["maxFailed"]
        and skipped <= expected["maxSkipped"]
    )
    return ("PASS" if passed else "FAIL"), sorted(item["evidenceId"] for item in evidence)


def review_checkpoint_checks(
    review: dict[str, Any], contract: dict[str, Any], evidence_by_case: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    automated = {item["id"]: item for item in contract["acceptanceCheckpoints"] if item["type"] == "AUTOMATED"}
    results = review["checkpointResults"]
    result_ids = [item["checkpointId"] for item in results]
    duplicate = sorted(item for item, count in Counter(result_ids).items() if count > 1)
    missing = sorted(set(automated) - set(result_ids))
    unknown = sorted(set(result_ids) - set(automated))
    if duplicate or missing or unknown:
        raise ControlError("HC-CHECKPOINT-REVIEW-CLOSURE", "review must report every automated checkpoint exactly once", details={"duplicate": duplicate, "missing": missing, "unknown": unknown})
    findings = {item["id"]: item for item in review["findings"]}
    checks: list[dict[str, Any]] = []
    any_deviation = False
    for result in results:
        checkpoint_item = automated[result["checkpointId"]]
        observed, evidence_ids = derive_checkpoint_result(checkpoint_item, evidence_by_case)
        expected_status = checkpoint_item["expected"]["status"]
        matches = (
            result["expectedStatus"] == expected_status
            and result["observedStatus"] == observed
            and sorted(result["evidenceIds"]) == evidence_ids
        )
        checks.append(check("HC-CHECKPOINT-RESULT-MISMATCH", "PASS" if matches else "FAIL", "review checkpoint result matches controller-derived evidence" if matches else "review checkpoint result differs from controller-derived evidence", checkpointId=result["checkpointId"], derivedObservedStatus=observed, derivedEvidenceIds=evidence_ids))
        deviates = observed != expected_status
        any_deviation = any_deviation or deviates
        finding_id = result.get("deviationFindingId")
        finding_ok = (
            (not deviates and finding_id is None)
            or (
                deviates
                and isinstance(finding_id, str)
                and finding_id in findings
                and result["checkpointId"] in findings[finding_id].get("checkpointRefs", [])
            )
        )
        checks.append(check("HC-CHECKPOINT-DEVIATION-FINDING", "PASS" if finding_ok else "FAIL", "checkpoint deviation and finding are consistent" if finding_ok else "checkpoint deviation lacks its candidate-bound finding", checkpointId=result["checkpointId"]))
    result_consistent = (review["result"] == "FAIL") if any_deviation else (review["result"] == "PASS")
    checks.append(check("HC-CHECKPOINT-REVIEW-TOTAL", "PASS" if result_consistent else "FAIL", "review total result agrees with checkpoint results" if result_consistent else "review total result conflicts with checkpoint results"))
    checks.append(check("HC-AUDIT-STOP-CLOSURE", "PASS", "all required automated checkpoints were reported; bounded audit must stop"))
    exploratory = [item for item in review["findings"] if item["classification"] in EXPLORATORY_FINDING_CLASSES]
    budget_ok = len(exploratory) <= contract["auditPolicy"]["maxExploratoryFindings"]
    checks.append(check("HC-AUDIT-EXPLORATION-BUDGET", "PASS" if budget_ok else "FAIL", "exploratory findings stay within the candidate budget" if budget_ok else "exploratory finding budget exceeded", count=len(exploratory), maximum=contract["auditPolicy"]["maxExploratoryFindings"]))
    return checks


def owner_checkpoint_checks(decision: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    human = {item["id"] for item in contract["acceptanceCheckpoints"] if item["type"] == "HUMAN" and CLAIMS.index(item["requiredForClaim"]) <= CLAIMS.index("ACCEPTED")}
    decisions = decision["checkpointDecisions"]
    ids = [item["checkpointId"] for item in decisions]
    duplicate = sorted(item for item, count in Counter(ids).items() if count > 1)
    missing = sorted(human - set(ids))
    unknown = sorted(set(ids) - human)
    rejected = sorted(item["checkpointId"] for item in decisions if item["decision"] == "REJECT")
    closed = not duplicate and not missing and not unknown and not rejected
    return [check("HC-CHECKPOINT-HUMAN-DECISION", "PASS" if closed else "BLOCKED", "one owner decision closes every applicable human checkpoint" if closed else "human checkpoint decision set is incomplete, duplicated, unknown, or rejected", duplicate=duplicate, missing=missing, unknown=unknown, rejected=rejected)]


def finding_structure_checks(
    finding: dict[str, Any], contract: dict[str, Any], objective_lock: dict[str, Any], claim: str,
) -> list[dict[str, Any]]:
    classification = finding["classification"]
    task_objectives = set(contract["objectiveRefs"])
    project_objectives = set(objective_lock["objectiveIds"])
    failure_modes = set(objective_lock["failureModeIds"])
    checkpoints = checkpoint_by_id(contract)
    checkpoint_refs = set(finding["checkpointRefs"])
    objective_refs = set(finding["objectiveRefs"])
    core_refs = set(finding["coreControlRefs"])
    evidence_ok = bool(finding["evidenceRefs"]) if classification in {"CURRENT_GOAL_DEFECT", "MINIMUM_CORE_VIOLATION", "SAFETY_OVERRIDE"} else True

    task_scope_ok = True
    core_ok = True
    if classification == "CURRENT_GOAL_DEFECT":
        task_scope_ok = bool(checkpoint_refs) and checkpoint_refs.issubset(checkpoints) and bool(objective_refs) and objective_refs.issubset(task_objectives)
        if task_scope_ok:
            task_scope_ok = all(objective_refs & set(checkpoints[item]["objectiveRefs"]) for item in checkpoint_refs)
    elif classification == "MINIMUM_CORE_VIOLATION":
        core_ok = bool(core_refs) and core_refs.issubset(MINIMUM_CORE_CONTROL_IDS)
    elif classification == "SAFETY_OVERRIDE":
        task_scope_ok = bool(objective_refs & failure_modes) and objective_refs.issubset(failure_modes)
    elif classification == "HUMAN_DECISION":
        task_scope_ok = objective_refs.issubset(task_objectives | project_objectives | failure_modes)
    else:
        task_scope_ok = objective_refs.issubset(project_objectives | failure_modes)

    claim_scope_ok = _upward_closed(finding["affectedClaims"]) if classification in {"CURRENT_GOAL_DEFECT", "MINIMUM_CORE_VIOLATION", "SAFETY_OVERRIDE", "HUMAN_DECISION"} else not finding["affectedClaims"] or _upward_closed(finding["affectedClaims"])
    checks = [
        check("HC-FINDING-TASK-SCOPE", "PASS" if task_scope_ok and evidence_ok else "FAIL", "finding is mapped to the permitted current-task or safety scope" if task_scope_ok and evidence_ok else "finding cannot enter this task scope", findingId=finding["id"]),
        check("HC-FINDING-CLAIM-SCOPE", "PASS" if claim_scope_ok else "FAIL", "affected claims form an upward closure" if claim_scope_ok else "affected claims are not upward closed", findingId=finding["id"]),
        check("HC-FINDING-CORE-REF", "PASS" if core_ok else "FAIL", "minimum-core finding cites a fixed core control" if core_ok else "minimum-core finding lacks a valid fixed core control", findingId=finding["id"]),
    ]
    structure_ok = all(item["status"] == "PASS" for item in checks)
    blocks = False
    if structure_ok and claim_is_affected(finding, claim):
        blocks = classification in {"CURRENT_GOAL_DEFECT", "MINIMUM_CORE_VIOLATION", "SAFETY_OVERRIDE", "HUMAN_DECISION"}
    checks.append(check("HC-FINDING-CLAIM-ADMISSION", "BLOCKED" if blocks else "PASS", "open admitted finding blocks this explicitly affected claim" if blocks else "finding is closed, advisory, out of task scope, or unrelated to this claim", findingId=finding["id"], classification=classification, claim=claim))
    return checks
