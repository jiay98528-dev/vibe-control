#!/usr/bin/env python3
"""Prove integrity-bound text is byte-stable in a Windows-style first checkout."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT_PATH = ".vibe-control/evidence/CRLF-PROBE.transcript.txt"


def invoke(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def run(*args: str, cwd: Path | None = None) -> str:
    value = invoke(*args, cwd=cwd)
    if value.returncode:
        raise RuntimeError(f"exit={value.returncode}: {' '.join(args)}\nstdout={value.stdout}\nstderr={value.stderr}")
    return value.stdout


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_runtime(clone: Path) -> None:
    runtime = clone / "assets" / "project-control" / "runtime"
    manifest = json.loads((runtime / "runtime-manifest.json").read_text(encoding="utf-8"))
    for ref in manifest["files"]:
        path = runtime / ref["path"]
        if not path.is_file() or path.stat().st_size != ref["bytes"] or sha(path) != ref["sha256"]:
            raise AssertionError(f"runtime manifest drift: {ref['path']}")


def verify_package(clone: Path, *, expect_pass: bool) -> dict:
    result = invoke(sys.executable, str(clone / "scripts" / "build_manifest.py"), "--root", str(clone), "--verify")
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"manifest verifier did not return JSON: {result.stdout!r}") from exc
    if report.get("checkId") not in {"PKG-MANIFEST-VERIFY", "RUNTIME-MANIFEST-VERIFY"}:
        raise AssertionError(f"manifest verifier omitted a stable check ID: {report}")
    if expect_pass and (result.returncode != 0 or report.get("status") != "PASS"):
        raise AssertionError(f"exact candidate checkout failed integrity: {report}")
    if not expect_pass and result.returncode == 0 and report.get("status") != "PASS":
        raise AssertionError(f"manifest verifier exit/status contradiction: {report}")
    if not expect_pass and result.returncode not in {0, 1}:
        raise AssertionError(f"manifest verifier used an unstable exit code: {result.returncode}")
    return {"exitCode": result.returncode, "report": report}


def copy_fixture(source: Path) -> tuple[Path, str, str]:
    """Create legacy and fixed commits without consuming installed dogfood state."""
    fixture = source / "fixture-source"
    shutil.copytree(
        ROOT,
        fixture,
        ignore=shutil.ignore_patterns(".git", ".vibe-control", "__pycache__"),
    )
    transcript = fixture / TRANSCRIPT_PATH
    transcript.parent.mkdir(parents=True)
    transcript.write_bytes(b"case=CRLF-PROBE\nstatus=PASS\n")
    run("git", "init", str(fixture))
    run("git", "-C", str(fixture), "config", "user.email", "fixture@example.invalid")
    run("git", "-C", str(fixture), "config", "user.name", "vibe-control fixture")

    attributes_path = fixture / ".gitattributes"
    fixed_attributes = attributes_path.read_text(encoding="utf-8")
    legacy_attributes = fixed_attributes.replace("*.txt -text\n", "")
    attributes_path.write_text(legacy_attributes, encoding="utf-8", newline="\n")
    run(sys.executable, str(fixture / "scripts" / "build_manifest.py"), "--root", str(fixture))
    run("git", "-C", str(fixture), "add", "--all")
    run("git", "-C", str(fixture), "commit", "-m", "fixture: legacy text policy")
    legacy_commit = run("git", "-C", str(fixture), "rev-parse", "HEAD").strip()

    attributes_path.write_text(fixed_attributes, encoding="utf-8", newline="\n")
    run(sys.executable, str(fixture / "scripts" / "build_manifest.py"), "--root", str(fixture))
    run("git", "-C", str(fixture), "add", "--all")
    run("git", "-C", str(fixture), "commit", "-m", "fixture: fixed LF text policy")
    candidate_commit = run("git", "-C", str(fixture), "rev-parse", "HEAD").strip()
    return fixture, legacy_commit, candidate_commit


def assert_lf_checkout(clone: Path, expected: bytes) -> dict:
    attributes = run("git", "-C", str(clone), "check-attr", "text", "eol", "--", TRANSCRIPT_PATH)
    if "text: unset" not in attributes:
        raise AssertionError(f"transcript is not protected from checkout conversion: {attributes!r}")
    checked_out = (clone / TRANSCRIPT_PATH).read_bytes()
    blob = invoke("git", "-C", str(clone), "show", f"HEAD:{TRANSCRIPT_PATH}").stdout.encode("utf-8")
    if checked_out != expected or blob != expected or b"\r\n" in checked_out:
        eol_state = run("git", "-C", str(clone), "ls-files", "--eol", "--", TRANSCRIPT_PATH)
        autocrlf = run("git", "-C", str(clone), "config", "--get", "core.autocrlf").strip()
        raise AssertionError(
            "Windows first checkout changed integrity-bound transcript bytes: "
            f"expected={expected!r}, checkout={checked_out!r}, blob={blob!r}, attrs={attributes!r}, "
            f"autocrlf={autocrlf!r}, eolState={eol_state!r}"
        )
    return {"sha256": hashlib.sha256(checked_out).hexdigest(), "bytes": len(checked_out)}


def test_crlf_checkout() -> list[dict]:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    if "*.txt -text" not in attributes:
        raise AssertionError("all .txt artifacts must be protected from checkout conversion before candidate materialization")
    cases: list[dict] = [{"id": "CRLF-TXT-POLICY", "status": "PASS", "observation": "txt-byte-preserved"}]
    with tempfile.TemporaryDirectory(prefix="vibe-control-crlf-") as temp:
        fixture, legacy_commit, candidate_commit = copy_fixture(Path(temp))
        expected = (fixture / TRANSCRIPT_PATH).read_bytes()
        clone = Path(temp) / "windows-first-checkout"
        run("git", "-c", "core.autocrlf=true", "clone", "--no-local", str(fixture), str(clone))
        run("git", "-C", str(clone), "config", "core.autocrlf", "true")
        run("git", "-C", str(clone), "checkout", "--detach", candidate_commit)
        transcript = assert_lf_checkout(clone, expected)
        verify_runtime(clone)
        package = verify_package(clone, expect_pass=True)
        if run("git", "-C", str(clone), "status", "--porcelain").strip():
            raise AssertionError("Windows first checkout is not clean")
        cases.append({
            "id": "CRLF-WINDOWS-FIRST-DOGFOOD-TRANSCRIPT",
            "status": "PASS",
            "observation": "byte-stable",
            "transcript": transcript,
        })
        cases.append({
            "id": "CRLF-WINDOWS-FIRST-PACKAGE-MANIFEST",
            "status": "PASS",
            "observation": "manifest-valid",
            "manifest": package["report"],
        })

        legacy = Path(temp) / "legacy-first-checkout"
        run("git", "-c", "core.autocrlf=true", "clone", "--no-local", "--no-checkout", str(fixture), str(legacy))
        run("git", "-C", str(legacy), "config", "core.autocrlf", "true")
        run("git", "-C", str(legacy), "checkout", "--detach", legacy_commit)
        legacy_result = verify_package(legacy, expect_pass=False)
        run("git", "-C", str(legacy), "checkout", "--detach", candidate_commit)
        recovered = verify_package(legacy, expect_pass=False)
        cases.append({
            "id": "CRLF-LEGACY-FIRST-FAIL-CLOSED",
            "status": "PASS",
            "observation": "legacy-drift-detected" if legacy_result["exitCode"] else "legacy-manifest-valid",
            "manifest": legacy_result["report"],
            "note": "The manifest result, not clean Git status, is authoritative for the legacy materialization.",
        })
        cases.append({
            "id": "CRLF-LEGACY-TO-CANDIDATE-FAIL-CLOSED",
            "status": "PASS",
            "observation": "manifest-valid" if recovered["exitCode"] == 0 else "drift-detected",
            "manifest": recovered["report"],
            "note": "A pre-existing CRLF worktree must either recover byte-for-byte or remain explicitly blocked.",
        })
    return cases


def main() -> int:
    cases = test_crlf_checkout()
    print(json.dumps({
        "status": "PASS",
        "test": "test_crlf_checkout",
        "counters": {"total": len(cases), "passed": len(cases), "failed": 0},
        "cases": cases,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "test": "test_crlf_checkout", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
