#!/usr/bin/env python3
"""Narrow regressions for standalone public report envelopes."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIELDS = {
    "projectPurpose",
    "whatWasDone",
    "whatWorksNow",
    "whatStillDoesNotWork",
    "userImpact",
    "canContinue",
    "canRelease",
}
FORBIDDEN = re.compile(
    r"\b(?:schema|claim|commit|tree|hash)\b|\b(?:HC|VC|CTRL|KO|KF|CP|CASE)-[A-Za-z0-9]|"
    r"哈希|控制面|声明等级|候选提交|目录树|门禁|审计|运行时|工作树|执行器|证据链|架构",
    re.IGNORECASE,
)


def run_report(command: list[str], cwd: Path, expected: set[int]) -> dict:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode in expected, (command, result.returncode, result.stdout, result.stderr)
    assert not result.stderr.strip(), (command, result.stderr)
    assert "Traceback" not in result.stdout and "Traceback" not in result.stderr
    report = json.loads(result.stdout)
    assert isinstance(report, dict) and list(report)[-1] == "plainLanguage", command
    plain = report["plainLanguage"]
    assert isinstance(plain, dict) and set(plain) == FIELDS, command
    assert all(isinstance(value, str) and value.strip() for value in plain.values()), command
    assert not any(FORBIDDEN.search(value) for value in plain.values()), (command, plain)
    return report


def main() -> int:
    cases = 0
    with tempfile.TemporaryDirectory(prefix="vibe-control-plain-reports-", ignore_cleanup_errors=True) as temp_name:
        temp = Path(temp_name)
        project_root = ROOT.parent

        path_script = SCRIPTS / "check_audit_path.py"
        run_report([sys.executable, str(path_script), "--source", str(project_root), "--candidate", "HEAD", "--audit-root", str(temp / "audit")], temp, {0})
        run_report([sys.executable, str(path_script), "--source", str(project_root), "--candidate", "missing-candidate", "--audit-root", str(temp / "audit")], temp, {2})
        run_report([sys.executable, str(path_script), "--unknown"], temp, {3})
        cases += 3

        package = temp / "portable"
        shutil.copytree(
            ROOT,
            package,
            ignore=shutil.ignore_patterns(".git", ".vibe-control", "__pycache__", "*.pyc"),
        )
        builder = package / "scripts" / "build_manifest.py"
        run_report([sys.executable, str(builder), "--root", str(package)], temp, {0})
        run_report([sys.executable, str(builder), "--root", str(package), "--verify"], temp, {0})
        (package / "SKILL.md").write_text((package / "SKILL.md").read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8", newline="\n")
        run_report([sys.executable, str(builder), "--root", str(package), "--verify"], temp, {1})
        run_report([sys.executable, str(builder), "--unknown"], temp, {3})
        cases += 4

        package_validator = SCRIPTS / "validate_package_release.py"
        run_report([sys.executable, str(package_validator), "--skill-root", str(ROOT)], temp, {0, 2, 3})
        blocked = run_report([sys.executable, "-S", str(package_validator), "--skill-root", str(ROOT)], temp, {2})
        assert blocked["status"] == "BLOCKED" and blocked["readiness"] == "DEPENDENCY_BLOCKED"
        run_report([sys.executable, str(package_validator), "--unknown"], temp, {3})
        cases += 3

        matrix_validator = SCRIPTS / "validate_assurance_matrix.py"
        run_report([sys.executable, str(matrix_validator), "--skill-root", str(ROOT)], temp, {0, 3})
        run_report([sys.executable, str(matrix_validator), "--skill-root", str(ROOT), "--matrix", "missing.json"], temp, {3})
        run_report([sys.executable, str(matrix_validator), "--unknown"], temp, {3})
        cases += 3

    print(json.dumps({
        "status": "PASS",
        "suite": "v040-public-plain-reports",
        "counters": {"total": cases, "passed": cases, "failed": 0, "skipped": 0},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
