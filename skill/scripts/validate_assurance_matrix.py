#!/usr/bin/env python3
"""Validate assurance implementation closure without granting package release."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from public_report import JsonArgumentError, JsonArgumentParser, emit, finalize


REQUIRED_FINDINGS = {f"F-{index:02d}" for index in range(1, 9)}
REQUIRED_ASSURANCE_CONTROL_IDS = frozenset({
    "CTRL-ASSURE-001", "CTRL-ASSURE-002", "CTRL-ASSURE-003", "CTRL-ASSURE-004",
    "CTRL-ASSURE-005", "CTRL-ASSURE-006", "CTRL-ASSURE-007", "CTRL-ASSURE-008",
    "CTRL-CONFIRMED-001", "CTRL-CONFIRMED-002", "CTRL-CONFIRMED-003",
    "CTRL-CONFIRMED-004", "CTRL-CONFIRMED-005", "CTRL-CONFIRMED-006",
    "CTRL-CONFIRMED-007", "CTRL-CONFIRMED-008", "CTRL-CONFIRMED-009",
    "CTRL-CONFIRMED-010", "CTRL-CONFIRMED-011", "CTRL-CONFIRMED-012",
    "CTRL-CONFIRMED-013", "CTRL-CONFIRMED-014", "CTRL-CONFIRMED-015",
    "CTRL-CONFIRMED-016", "CTRL-CONFIRMED-017", "CTRL-CONFIRMED-018",
    "CTRL-CONFIRMED-019", "CTRL-CONFIRMED-020", "CTRL-CONFIRMED-021",
    "CTRL-CONFIRMED-022", "CTRL-CONFIRMED-023", "CTRL-CONFIRMED-024",
    "CTRL-CONFIRMED-025", "CTRL-CONFIRMED-026", "CTRL-CONFIRMED-027",
    "CTRL-CONFIRMED-028", "CTRL-CONFIRMED-029", "CTRL-CONFIRMED-030",
    "CTRL-CONFIRMED-031", "CTRL-CONFIRMED-032", "CTRL-CONFIRMED-033",
    "CTRL-CONFIRMED-034", "CTRL-CONFIRMED-035", "CTRL-CONFIRMED-036",
    "CTRL-CONFIRMED-037", "CTRL-CONFIRMED-038", "CTRL-CONFIRMED-039",
    "CTRL-CONFIRMED-040", "CTRL-CONFIRMED-041", "CTRL-CONFIRMED-042",
    "CTRL-CONFIRMED-043",
})
STATUSES = {"NOT_IMPLEMENTED", "PARTIAL", "IMPLEMENTED"}


def plain_language(ok: bool) -> dict[str, str]:
    return {
        "projectPurpose": "确认这套开发工具承诺的保护措施是否都有对应实现和检查。",
        "whatWasDone": "已核对保护措施、实现位置和对应测试之间的关系。",
        "whatWorksNow": "记录完整时，可以继续进行更独立的候选核对。" if ok else "当前记录不足以说明这些保护措施都已实现。",
        "whatStillDoesNotWork": "这项核对不代表工具已经可以正式交付。" if ok else "仍有实现、测试或记录需要补齐。",
        "userImpact": "可以继续后续核对，但不能把内部记录当作最终发行结论。" if ok else "缺口未补齐前，不应依赖这套工具给出高把握结论。",
        "canContinue": "可以继续独立核对。" if ok else "应先补齐当前缺口。",
        "canRelease": "现在不能仅凭这项结果作为最终版本交付。",
    }


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _main_impl(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser()
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--matrix", default="references/controller-assurance-matrix.json")
    args = parser.parse_args(argv)

    root = Path(args.skill_root).resolve()
    matrix_path = (root / args.matrix).resolve()
    errors: list[dict[str, str]] = []
    try:
        matrix = json.loads(read_text(matrix_path))
    except Exception as exc:
        emit(finalize({"schemaVersion": "4.0", "status": "FAIL", "readiness": "DIAGNOSTIC", "errors": [{"id": "MATRIX-JSON", "message": str(exc)}]}, plain_language(False)))
        return 3

    if not isinstance(matrix, dict):
        errors.append({"id": "MATRIX-TYPE", "message": "matrix must be an object"})
        requirements = []
    else:
        requirements = matrix.get("requirements", [])
    if not isinstance(requirements, list):
        errors.append({"id": "MATRIX-REQUIREMENTS", "message": "requirements must be an array"})
        requirements = []

    seen_ids: set[str] = set()
    covered_findings: set[str] = set()
    open_requirements: list[str] = []
    open_items: list[str] = []
    pending_independent: list[str] = []
    test_sources = "\n".join(read_text(path) for path in sorted((root / "scripts").glob("test_*.py")) if path.is_file())
    confirmed_controls = matrix.get("confirmedControls", []) if isinstance(matrix, dict) else []
    if not isinstance(confirmed_controls, list):
        errors.append({"id": "MATRIX-CONFIRMED", "message": "confirmedControls must be an array"})
        confirmed_controls = []

    for item in requirements + confirmed_controls:
        if not isinstance(item, dict):
            errors.append({"id": "MATRIX-ITEM-TYPE", "message": "requirement must be an object"})
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            errors.append({"id": "MATRIX-ID", "message": "requirement id is missing"})
            continue
        if item_id in seen_ids:
            errors.append({"id": "MATRIX-DUPLICATE-ID", "message": item_id})
        seen_ids.add(item_id)
        if item in requirements:
            findings = item.get("findingIds", [])
            if not isinstance(findings, list) or not findings:
                errors.append({"id": "MATRIX-FINDING", "message": f"{item_id} has no findingIds"})
            else:
                covered_findings.update(value for value in findings if isinstance(value, str))
        status = item.get("implementationStatus")
        if status not in STATUSES:
            errors.append({"id": "MATRIX-STATUS", "message": f"{item_id}: {status}"})
            continue
        if status != "IMPLEMENTED":
            open_items.append(item_id)
            if item in requirements:
                open_requirements.append(item_id)
        refs = item.get("implementationRefs", [])
        tests = item.get("regressionTests", [])
        if not isinstance(refs, list) or not isinstance(tests, list) or not tests:
            errors.append({"id": "MATRIX-TRACE", "message": f"{item_id} needs arrays and at least one regression test"})
            continue
        for test_name in tests:
            if not isinstance(test_name, str) or f"def {test_name}(" not in test_sources:
                errors.append({"id": "MATRIX-TEST-MISSING", "message": f"{item_id}: {test_name}"})
        for relative in refs:
            if not isinstance(relative, str) or not (root / relative).is_file():
                errors.append({"id": "MATRIX-REF-MISSING", "message": f"{item_id}: {relative}"})
        if status == "IMPLEMENTED" and not refs:
            errors.append({"id": "MATRIX-IMPLEMENTATION-MISSING", "message": item_id})
        if status == "IMPLEMENTED" and item.get("independentValidation") not in {"PASS", "NOT_REQUIRED"}:
            pending_independent.append(item_id)

    missing_controls = sorted(REQUIRED_ASSURANCE_CONTROL_IDS - seen_ids)
    if missing_controls:
        errors.append({"id": "MATRIX-CONTROL-COVERAGE", "message": f"missing required controls {missing_controls}"})
    missing_findings = sorted(REQUIRED_FINDINGS - covered_findings)
    unexpected_findings = sorted(covered_findings - REQUIRED_FINDINGS)
    if missing_findings:
        errors.append({"id": "MATRIX-FINDING-COVERAGE", "message": f"missing {missing_findings}"})
    if unexpected_findings:
        errors.append({"id": "MATRIX-FINDING-UNKNOWN", "message": f"unknown {unexpected_findings}"})
    declared_formal = matrix.get("formalClaimsAllowed") is True if isinstance(matrix, dict) else False
    if declared_formal:
        errors.append({"id": "MATRIX-FORMAL-SOURCE", "message": "the assurance matrix cannot grant package-level formal readiness"})

    implementation_ready = not open_items and not pending_independent and not errors
    implementation_pending_external = not open_items and bool(pending_independent) and not errors
    readiness = "CONTROL_IMPLEMENTATION_READY" if implementation_ready else ("CONTROL_IMPLEMENTATION_PENDING_EXTERNAL_VALIDATION" if implementation_pending_external else "DIAGNOSTIC")

    report = {
        "schemaVersion": "4.0",
        "status": "PASS" if not errors else "FAIL",
        "readiness": readiness,
        "formalClaimsAllowed": False,
        "declaredFormalClaimsAllowed": declared_formal,
        "requirements": len(requirements),
        "openRequirements": open_requirements,
        "openItems": open_items,
        "pendingIndependentValidation": pending_independent,
        "errors": errors,
        "note": "PASS means control implementation is internally traceable. Only validate_package_release.py may combine it with an exact candidate audit and grant package readiness.",
    }
    emit(finalize(report, plain_language(not errors)))
    return 0 if not errors else 3


def main(argv: list[str] | None = None) -> int:
    try:
        return _main_impl(argv)
    except JsonArgumentError as exc:
        report = {
            "schemaVersion": "4.0",
            "status": "FAIL",
            "readiness": "DIAGNOSTIC",
            "formalClaimsAllowed": False,
            "errors": [{"id": "MATRIX-INVALID-ARGUMENTS", "message": str(exc)}],
        }
    except Exception as exc:
        report = {
            "schemaVersion": "4.0",
            "status": "FAIL",
            "readiness": "DIAGNOSTIC",
            "formalClaimsAllowed": False,
            "errors": [{
                "id": "MATRIX-INTERNAL-ERROR",
                "message": "matrix validation failed without exposing a traceback",
                "details": {"errorType": type(exc).__name__, "error": str(exc)},
            }],
        }
    emit(finalize(report, plain_language(False)))
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
