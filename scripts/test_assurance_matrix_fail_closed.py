#!/usr/bin/env python3
"""Prove that the matrix declaration cannot outrun implementation or review closure."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import build_manifest
import validate_assurance_matrix


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_assurance_matrix.py"
MATRIX = ROOT / "references" / "controller-assurance-matrix.json"


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def invoke(matrix: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--skill-root", str(ROOT), "--matrix", str(matrix)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    return result, json.loads(result.stdout)


def test_assurance_matrix_fail_closed() -> None:
    source = json.loads(MATRIX.read_text(encoding="utf-8-sig"))
    with tempfile.TemporaryDirectory(prefix="vibe-control-matrix-gate-") as temp:
        base = Path(temp)
        premature = json.loads(json.dumps(source))
        premature["formalClaimsAllowed"] = True
        first = premature["requirements"][0]
        first["independentValidation"] = "REQUIRED"
        premature_path = base / "premature.json"
        write_json(premature_path, premature)
        result, report = invoke(premature_path)
        error_ids = {item["id"] for item in report.get("errors", [])}
        if result.returncode != 3 or report.get("formalClaimsAllowed") is not False or "MATRIX-FORMAL-SOURCE" not in error_ids:
            raise AssertionError(f"premature formal declaration was not rejected: {report}")

        closed = json.loads(json.dumps(source))
        closed["formalClaimsAllowed"] = False
        for item in [*closed["requirements"], *closed["confirmedControls"]]:
            item["implementationStatus"] = "IMPLEMENTED"
            item["independentValidation"] = "PASS"
        closed_path = base / "closed.json"
        write_json(closed_path, closed)
        result, report = invoke(closed_path)
        if result.returncode != 0 or report.get("formalClaimsAllowed") is not False or report.get("readiness") != "CONTROL_IMPLEMENTATION_READY":
            raise AssertionError(f"closed assurance implementation did not become externally auditable: {report}")

        self_grant = json.loads(json.dumps(closed))
        self_grant["formalClaimsAllowed"] = True
        self_grant_path = base / "self-grant.json"
        write_json(self_grant_path, self_grant)
        result, report = invoke(self_grant_path)
        if result.returncode != 3 or "MATRIX-FORMAL-SOURCE" not in {item["id"] for item in report.get("errors", [])}:
            raise AssertionError(f"matrix self-grant was not rejected: {report}")

        deleted = json.loads(json.dumps(closed))
        deleted["confirmedControls"] = [item for item in deleted["confirmedControls"] if item.get("id") != "CTRL-CONFIRMED-011"]
        deleted_path = base / "deleted-control.json"
        write_json(deleted_path, deleted)
        result, report = invoke(deleted_path)
        error_ids = {item["id"] for item in report.get("errors", [])}
        if result.returncode != 3 or report.get("formalClaimsAllowed") is not False or "MATRIX-CONTROL-COVERAGE" not in error_ids:
            raise AssertionError(f"deleted required control was not rejected: {report}")


def test_assurance_required_ids_consistent() -> None:
    runtime_root = ROOT / "assets" / "project-control" / "runtime"
    sys.path.insert(0, str(runtime_root))
    try:
        from vibe_runtime import controller, package_release
    finally:
        sys.path.pop(0)
    expected = set(validate_assurance_matrix.REQUIRED_ASSURANCE_CONTROL_IDS)
    if (
        set(build_manifest.REQUIRED_ASSURANCE_CONTROL_IDS) != expected
        or set(controller.REQUIRED_ASSURANCE_CONTROL_IDS) != expected
        or set(package_release.REQUIRED_PACKAGE_CONTROL_IDS) != expected
    ):
        raise AssertionError("required assurance control IDs drifted between builder, validator, runtime, and package release validator")
    matrix = json.loads(MATRIX.read_text(encoding="utf-8-sig"))
    actual = {item.get("id") for item in [*matrix["requirements"], *matrix["confirmedControls"]]}
    if not expected.issubset(actual):
        raise AssertionError(f"source matrix omits required controls: {sorted(expected - actual)}")


def test_confirmed_control_implementation_fail_closed() -> None:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8-sig"))
    matrix["formalClaimsAllowed"] = False
    for item in [*matrix["requirements"], *matrix["confirmedControls"]]:
        item["implementationStatus"] = "IMPLEMENTED"
        item["independentValidation"] = "PASS"
    target = next(item for item in matrix["confirmedControls"] if item.get("id") == "CTRL-CONFIRMED-001")
    target["implementationStatus"] = "NOT_IMPLEMENTED"
    with tempfile.TemporaryDirectory(prefix="vibe-control-confirmed-open-", ignore_cleanup_errors=True) as temp:
        path = Path(temp) / "confirmed-open.json"
        write_json(path, matrix)
        result, report = invoke(path)
    error_ids = {item["id"] for item in report.get("errors", [])}
    if (
        result.returncode != 0
        or report.get("formalClaimsAllowed") is not False
        or report.get("readiness") != "DIAGNOSTIC"
        or "CTRL-CONFIRMED-001" not in report.get("openItems", [])
    ):
        raise AssertionError(f"open confirmed control did not fail closed: {report}")


def test_structural_invalidity_blocks_manifest_readiness() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-structural-matrix-", ignore_cleanup_errors=True) as temp:
        copied = Path(temp) / "skill"
        shutil.copytree(ROOT, copied, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        matrix_path = copied / "references" / "controller-assurance-matrix.json"
        base = json.loads(matrix_path.read_text(encoding="utf-8-sig"))
        base["formalClaimsAllowed"] = False
        for item in [*base["requirements"], *base["confirmedControls"]]:
            item["implementationStatus"] = "IMPLEMENTED"
            item["independentValidation"] = "PASS"
        mutations = {
            "duplicate-id": lambda value: value["confirmedControls"].append(json.loads(json.dumps(value["confirmedControls"][-1]))),
            "non-object-item": lambda value: value["confirmedControls"].append("not-an-object"),
            "missing-ref": lambda value: value["confirmedControls"][0].update({"implementationRefs": []}),
            "bad-ref": lambda value: value["confirmedControls"][0].update({"implementationRefs": ["missing-assurance-ref"]}),
            "missing-test": lambda value: value["confirmedControls"][0].update({"regressionTests": []}),
            "bad-test": lambda value: value["confirmedControls"][0].update({"regressionTests": ["test_that_does_not_exist"]}),
            "missing-finding": lambda value: value["requirements"][0].update({"findingIds": []}),
            "unknown-finding": lambda value: value["requirements"][0].update({"findingIds": [*value["requirements"][0]["findingIds"], "F-UNKNOWN"]}),
        }
        for name, mutate in mutations.items():
            matrix = json.loads(json.dumps(base))
            mutate(matrix)
            write_json(matrix_path, matrix)
            manifest = build_manifest.build(copied)
            if manifest.get("maturity") == "FORMAL_GATE_READY" or manifest.get("assuranceValidation", {}).get("status") != "FAIL":
                raise AssertionError(f"manifest builder ignored structurally invalid assurance matrix: {name}")
        malformed_documents = {
            "top-level-list": [],
            "requirements-not-array": {**json.loads(json.dumps(base)), "requirements": {}},
            "confirmed-not-array": {**json.loads(json.dumps(base)), "confirmedControls": {}},
        }
        for name, matrix in malformed_documents.items():
            write_json(matrix_path, matrix)
            manifest = build_manifest.build(copied)
            if manifest.get("maturity") == "FORMAL_GATE_READY" or manifest.get("assuranceValidation", {}).get("status") != "FAIL":
                raise AssertionError(f"manifest builder ignored malformed assurance document: {name}")
        matrix_path.write_text("{not-json\n", encoding="utf-8", newline="\n")
        manifest = build_manifest.build(copied)
        if manifest.get("maturity") == "FORMAL_GATE_READY" or manifest.get("assuranceValidation", {}).get("status") != "FAIL":
            raise AssertionError("manifest builder ignored invalid assurance JSON")


def main() -> int:
    try:
        test_assurance_matrix_fail_closed()
        test_assurance_required_ids_consistent()
        test_confirmed_control_implementation_fail_closed()
        test_structural_invalidity_blocks_manifest_readiness()
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "test": "test_assurance_matrix_fail_closed", "counters": {"total": 17, "passed": 0, "failed": 1}, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "PASS", "test": "test_assurance_matrix_fail_closed", "counters": {"total": 17, "passed": 17, "failed": 0}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
