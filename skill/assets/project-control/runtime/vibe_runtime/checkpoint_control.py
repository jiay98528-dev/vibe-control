"""Deterministic checkpoint and execution-plan helpers for Schema 4.0."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any

from .common import ControlError, canonical_bytes, check, sha256_bytes


CLAIMS = ("DIAGNOSTIC", "DEVELOPMENT_CHECKED", "VERIFIED", "ACCEPTED", "RELEASE_READY")
DEFAULT_AUDIT_POLICY = {
    "strategy": "PROJECT_DERIVED",
    "maxExploratoryFindings": 3,
    "stopCondition": "ALL_REQUIRED_CHECKPOINTS_REPORTED",
    "requiredReviewRoles": ["INDEPENDENT_AUDITOR"],
    "triggerReasons": ["MILESTONE_CANDIDATE_READY"],
}
# Historical 3.2 fixture import; current task construction uses the project-derived
# DEFAULT_AUDIT_POLICY and may replace its roles/triggers/form before locking.
AUDIT_POLICY = DEFAULT_AUDIT_POLICY
MAX_EXPLORATORY_FINDINGS = 3
EXPLORATORY_FINDING_CLASSES = {
    "PROCESS_WARNING", "INVESTIGATION", "FUTURE_PROPOSAL", "OUT_OF_SCOPE",
}
MINIMUM_CORE_CONTROL_IDS = {
    "RULE-CORE-OBSERVABLE-CANDIDATE",
    "RULE-CORE-FAILURE-CONSERVATION",
}
SCORECARD_WEIGHTS = {
    "FUNCTIONALITY": 40,
    "ROBUSTNESS_SECURITY": 25,
    "AUDIT": 20,
    "PROCESS": 15,
}
GUARD_EFFECTS = {
    "MUTATION": "ACTION_GUARD",
    "CLAIM": "CLAIM_GUARD",
    "PROCESS": "ADVISORY",
    "HUMAN": "HUMAN_DECISION",
    "ENVIRONMENT": "ENVIRONMENT_BLOCKED",
}
PLAIN_LANGUAGE_FIELDS = (
    "projectPurpose", "whatWasDone", "whatWorksNow", "whatStillDoesNotWork",
    "userImpact", "canContinue", "canRelease",
)
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


def execution_plan_payload(contract: dict[str, Any]) -> dict[str, Any]:
    """Return every new 4.0 planning surface that must invalidate downstream facts."""
    return {
        "milestones": contract["milestones"],
        "scorecardPlan": contract["scorecardPlan"],
        "verificationStrategy": contract["verificationStrategy"],
        "guardPolicy": contract["guardPolicy"],
        "reportingPolicy": contract["reportingPolicy"],
    }


def execution_plan_sha256(contract: dict[str, Any]) -> str:
    return sha256_bytes(canonical_bytes(execution_plan_payload(contract)))


def guard_effects(contract: dict[str, Any]) -> dict[str, str]:
    return {item["scope"]: item["effect"] for item in contract["guardPolicy"]["guards"]}


def review_requirement(contract: dict[str, Any]) -> dict[str, Any]:
    """Return the content-locked review form without deriving it from risk.

    Risk continues to govern irreversible/external actions elsewhere.  The
    review workflow itself is a project decision captured by both the audit
    policy and verification strategy, so two tasks at the same risk may choose
    different, explicit review forms.
    """
    policy = contract["auditPolicy"]
    auditor = contract["verificationStrategy"]["auditor"]
    return {
        "required": auditor["required"],
        "form": auditor["form"],
        "roles": list(policy["requiredReviewRoles"]),
        "triggerReasons": list(policy["triggerReasons"]),
        "stopCondition": policy["stopCondition"],
    }


def execution_plan_checks(contract: dict[str, Any], catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Enforce cross-field closure for milestones, scorecards, verification and guards."""
    checkpoints = {item["id"]: item for item in contract["acceptanceCheckpoints"]}
    task_objectives = set(contract["objectiveRefs"])
    task_cases = {item["id"]: item for item in catalog["cases"] if item["id"] in contract["requiredCaseIds"]}

    milestone_ids = {item["id"] for item in contract["milestones"]}
    if len(milestone_ids) != len(contract["milestones"]):
        raise ControlError("HC-MILESTONE-CLOSURE", "milestone IDs must be unique")
    milestone_checkpoint_counts: Counter[str] = Counter()
    work_node_ids: set[str] = set()
    work_node_minimum_checks: dict[str, set[str]] = {}
    dependency_graph: dict[str, set[str]] = {}
    for milestone in contract["milestones"]:
        if not set(milestone["objectiveRefs"]).issubset(task_objectives):
            raise ControlError("HC-MILESTONE-CLOSURE", "milestone objectives exceed the current task")
        dependencies = set(milestone["dependsOn"])
        if milestone["id"] in dependencies or not dependencies.issubset(milestone_ids):
            raise ControlError("HC-MILESTONE-CLOSURE", "milestone dependencies must reference other known milestones")
        dependency_graph[milestone["id"]] = dependencies
        for node in milestone["workNodes"]:
            if node["id"] in work_node_ids:
                raise ControlError("HC-MILESTONE-CLOSURE", "work-node IDs must be unique across the task")
            work_node_ids.add(node["id"])
            work_node_minimum_checks[node["id"]] = set(node["minimumChecks"])
            outside = sorted(set(node["allowedPaths"]) - set(contract["allowedPaths"]))
            if outside:
                raise ControlError("HC-MILESTONE-CLOSURE", "work-node paths must be selected from the task path envelope", details={"workNodeId": node["id"], "outside": outside})
        unknown = sorted(set(milestone["checkpointIds"]) - set(checkpoints))
        if unknown:
            raise ControlError("HC-MILESTONE-CLOSURE", "milestone references unknown checkpoints", details=unknown)
        for checkpoint_id in milestone["checkpointIds"]:
            milestone_checkpoint_counts[checkpoint_id] += 1
            if not set(checkpoints[checkpoint_id]["objectiveRefs"]) & set(milestone["objectiveRefs"]):
                raise ControlError("HC-MILESTONE-CLOSURE", "milestone and checkpoint do not share a task objective", details={"milestoneId": milestone["id"], "checkpointId": checkpoint_id})
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(milestone_id: str) -> None:
        if milestone_id in visiting:
            raise ControlError("HC-MILESTONE-CLOSURE", "milestone dependency graph must be acyclic")
        if milestone_id in visited:
            return
        visiting.add(milestone_id)
        for dependency in dependency_graph[milestone_id]:
            visit(dependency)
        visiting.remove(milestone_id)
        visited.add(milestone_id)

    for milestone_id in sorted(milestone_ids):
        visit(milestone_id)
    bad_milestone_coverage = {
        checkpoint_id: milestone_checkpoint_counts[checkpoint_id]
        for checkpoint_id in checkpoints
        if milestone_checkpoint_counts[checkpoint_id] != 1
    }
    if bad_milestone_coverage:
        raise ControlError("HC-MILESTONE-CLOSURE", "every checkpoint must belong to exactly one milestone", details=bad_milestone_coverage)

    scorecard = contract["scorecardPlan"]
    if scorecard["weights"] != SCORECARD_WEIGHTS:
        raise ControlError("HC-SCORECARD-CLOSURE", "scorecard weights must equal the fixed 40/25/20/15 allocation")
    item_ids: set[str] = set()
    categories: Counter[str] = Counter()
    scored_checkpoints: Counter[str] = Counter()
    for item in scorecard["items"]:
        if item["id"] in item_ids:
            raise ControlError("HC-SCORECARD-CLOSURE", "scorecard item IDs must be unique")
        item_ids.add(item["id"])
        categories[item["category"]] += 1
        unknown = sorted(set(item["checkpointIds"]) - set(checkpoints))
        if unknown:
            raise ControlError("HC-SCORECARD-CLOSURE", "scorecard item references unknown checkpoints", details=unknown)
        source_kinds = {source["kind"] for source in item["factSources"]}
        for source in item["factSources"]:
            refs = set(source["refs"])
            if source["kind"] == "CHECKPOINT" and not refs.issubset(checkpoints):
                raise ControlError("HC-SCORECARD-FACT-SOURCE", "scorecard CHECKPOINT facts reference unknown checkpoints", details=sorted(refs - set(checkpoints)))
            if source["kind"] == "CASE" and not refs.issubset(task_cases):
                raise ControlError("HC-SCORECARD-FACT-SOURCE", "scorecard CASE facts reference cases outside the task", details=sorted(refs - set(task_cases)))
            if source["kind"] == "CORE_CONTROL" and not refs.issubset(MINIMUM_CORE_CONTROL_IDS):
                raise ControlError("HC-SCORECARD-FACT-SOURCE", "scorecard CORE_CONTROL facts must cite the fixed minimum core", details=sorted(refs - MINIMUM_CORE_CONTROL_IDS))
        required_kinds = {
            "FUNCTIONALITY": {"CHECKPOINT", "CASE", "EVIDENCE"},
            "ROBUSTNESS_SECURITY": {"CASE", "EVIDENCE", "CORE_CONTROL"},
            "AUDIT": {"REVIEW"},
            "PROCESS": {"CORE_CONTROL"},
        }[item["category"]]
        if not source_kinds & required_kinds:
            raise ControlError("HC-SCORECARD-FACT-SOURCE", "scorecard category lacks an eligible independent fact source", details={"itemId": item["id"], "category": item["category"], "requiredKinds": sorted(required_kinds), "actualKinds": sorted(source_kinds)})
        scored_checkpoints.update(item["checkpointIds"])
    missing_categories = sorted(set(SCORECARD_WEIGHTS) - set(categories))
    missing_scored = sorted(set(checkpoints) - set(scored_checkpoints))
    if missing_categories or missing_scored:
        raise ControlError("HC-SCORECARD-CLOSURE", "scorecard must cover all four categories and every task checkpoint", details={"missingCategories": missing_categories, "missingCheckpoints": missing_scored})

    strategy = contract["verificationStrategy"]
    eligible_observations = set(strategy["eligibleObservations"])
    ineligible_cases = sorted(
        case_id for case_id, case in task_cases.items()
        if case.get("observation") not in eligible_observations
    )
    if ineligible_cases:
        raise ControlError("HC-VERIFICATION-STRATEGY", "verification strategy excludes a required case observation", details=ineligible_cases)
    mappings = strategy["checkpointCases"]
    mapped_ids = [item["checkpointId"] for item in mappings]
    automated = {item["id"]: set(item["caseIds"]) for item in checkpoints.values() if item["type"] == "AUTOMATED"}
    mapping_closed = len(mapped_ids) == len(set(mapped_ids)) and set(mapped_ids) == set(automated)
    if mapping_closed:
        mapping_closed = all(set(item["caseIds"]) == automated[item["checkpointId"]] for item in mappings)
    executor_closed = set(strategy["executor"]["caseIds"]) == set(contract["requiredCaseIds"])
    if not mapping_closed or not executor_closed:
        raise ControlError("HC-VERIFICATION-STRATEGY", "verification strategy must map every automated checkpoint and executor case exactly", details={"mappedCheckpoints": mapped_ids, "executorCases": strategy["executor"]["caseIds"]})
    quick_check_ids = {item["id"] for item in strategy["implementer"]["quickChecks"]}
    assertion_ids = {assertion["id"] for item in checkpoints.values() for assertion in item["assertions"]}
    known_minimum_checks = quick_check_ids | set(task_cases) | set(checkpoints) | assertion_ids
    unknown_work_checks = {
        node_id: sorted(values - known_minimum_checks)
        for node_id, values in work_node_minimum_checks.items()
        if values - known_minimum_checks
    }
    if len(quick_check_ids) != len(strategy["implementer"]["quickChecks"]) or unknown_work_checks:
        raise ControlError(
            "HC-VERIFICATION-STRATEGY",
            "work-node minimum checks must resolve to unique locked quick checks, cases, checkpoints or assertions",
            details={"unknownWorkChecks": unknown_work_checks},
        )
    review = review_requirement(contract)
    review_policy_closed = (
        review["required"] == bool(review["roles"] and review["triggerReasons"])
        and bool(review["roles"]) == bool(review["triggerReasons"])
        and strategy["auditor"]["stopCondition"] == review["stopCondition"]
        and ((review["required"] and review["form"] != "NONE") or (not review["required"] and review["form"] == "NONE"))
    )
    if not review_policy_closed:
        raise ControlError(
            "HC-VERIFICATION-REVIEW-POLICY",
            "review requirement, form, roles, triggers and stop condition must describe one locked project workflow",
            details=review,
        )

    guards = contract["guardPolicy"]["guards"]
    scopes = [item["scope"] for item in guards]
    actual_effects = {item["scope"]: item["effect"] for item in guards}
    if len(scopes) != len(set(scopes)) or actual_effects != GUARD_EFFECTS:
        raise ControlError("HC-GUARD-POLICY", "guard policy must bind each fixed scope to its fixed effect", details={"expected": GUARD_EFFECTS, "actual": actual_effects})
    reporting = contract["reportingPolicy"]
    reporting_ok = (
        reporting["orientation"] == "ZERO_CONTEXT_ORIENTATION"
        and reporting["progressMode"] == "NON_BLOCKING"
        and reporting["reviewPoint"] == "OWNER_REVIEW"
        and reporting["plainLanguageFields"] == list(PLAIN_LANGUAGE_FIELDS)
        and set(reporting["nextActions"]) == {"continue", "repair", "humanReview"}
        and all(reporting["nextActions"][name] for name in ("continue", "repair", "humanReview"))
    )
    if not reporting_ok:
        raise ControlError("HC-REPORTING-POLICY", "reporting policy must orient a new reader, use seven plain-language fields and provide three next-action classes")

    expected_hash = execution_plan_sha256(contract)
    actual_hash = contract["checkpointConfirmation"].get("executionPlanSha256")
    if actual_hash != expected_hash:
        raise ControlError("HC-EXECUTION-PLAN-HASH", "task confirmation does not bind the complete execution plan", details={"expected": expected_hash, "actual": actual_hash})
    return [
        check("HC-MILESTONE-CLOSURE", "PASS", "every checkpoint belongs to one bounded milestone"),
        check("HC-SCORECARD-CLOSURE", "PASS", "fixed weighted scorecard covers all categories and checkpoints"),
        check("HC-SCORECARD-FACT-SOURCE", "PASS", "scorecard categories bind distinct eligible fact sources"),
        check("HC-VERIFICATION-STRATEGY", "PASS", "checkpoint, quick-check, executor and auditor work are explicitly locked"),
        check("HC-VERIFICATION-REVIEW-POLICY", "PASS", "review form is project-derived and content-locked instead of inferred from risk", required=review["required"], form=review["form"]),
        check("HC-GUARD-POLICY", "PASS", "guard scopes have fixed action, claim, advisory, human, and environment effects"),
        check("HC-REPORTING-POLICY", "PASS", "reports orient a new reader and expose three concrete next-action classes"),
        check("HC-EXECUTION-PLAN-HASH", "PASS", "confirmation binds the complete execution plan", executionPlanSha256=expected_hash),
    ]


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
    audit_policy = contract["auditPolicy"]
    if (
        audit_policy["strategy"] != "PROJECT_DERIVED"
        or audit_policy["maxExploratoryFindings"] > MAX_EXPLORATORY_FINDINGS
        or not audit_policy["stopCondition"].strip()
    ):
        raise ControlError("HC-AUDIT-STOP-CLOSURE", "project-derived audit policy exceeds the bounded deterministic core")

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
        check("HC-AUDIT-STOP-CLOSURE", "PASS", "project-derived audit policy stays inside the bounded deterministic core"),
        *execution_plan_checks(contract, catalog),
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
    checks.append(check("HC-GUARD-PROCESS", "PASS", "ordinary process findings remain advisory and cannot create a claim blocker", effect=guard_effects(contract)["PROCESS"]))
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
    human_effect = guard_effects(contract)["HUMAN"]
    return [check("HC-CHECKPOINT-HUMAN-DECISION", "PASS" if closed else "BLOCKED", "one owner decision closes every applicable human checkpoint" if closed else "human checkpoint decision set is incomplete, duplicated, unknown, or rejected", duplicate=duplicate, missing=missing, unknown=unknown, rejected=rejected, effect=human_effect)]


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
