#!/usr/bin/env python3
"""Validate assurance implementation closure without granting package release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


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
})
STATUSES = {"NOT_IMPLEMENTED", "PARTIAL", "IMPLEMENTED"}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--matrix", default="references/controller-assurance-matrix.json")
    args = parser.parse_args()

    root = Path(args.skill_root).resolve()
    matrix_path = (root / args.matrix).resolve()
    errors: list[dict[str, str]] = []
    try:
        matrix = json.loads(read_text(matrix_path))
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "readiness": "DIAGNOSTIC", "errors": [{"id": "MATRIX-JSON", "message": str(exc)}]}, ensure_ascii=False))
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
        "status": "PASS" if not errors else "FAIL",
        "readiness": readiness,
        "formalClaimsAllowed": False,
        "declaredFormalClaimsAllowed": declared_formal,
        "requirements": len(requirements),
        "openRequirements": open_requirements,
        "openItems": open_items,
        "pendingIndependentValidation": pending_independent,
        "errors": errors,
        "note": "PASS means control implementation is internally traceable. Only validate_package_release.py may combine it with an exact candidate audit and grant package readiness."
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 3


if __name__ == "__main__":
    raise SystemExit(main())
