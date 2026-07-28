#!/usr/bin/env python3
"""Package-candidate audit closure and post-audit mutation regressions."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL_IDS = sorted({
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
    "CTRL-CONFIRMED-028",
})


def run(root: Path, *args: str, expect: int | None = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(list(args), cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if expect is not None and result.returncode != expect:
        raise AssertionError(f"exit={result.returncode}, expected={expect}: {' '.join(args)}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def git(root: Path, *args: str) -> str:
    return run(root, "git", *args).stdout.strip()


def write_json(path: Path, value: dict) -> bytes:
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(data)
    return data


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blob(root: Path, name: str, contents: bytes) -> tuple[str, str]:
    """Store a fixture-only immutable Git blob outside the package worktree."""
    path = root.parent / name
    path.write_bytes(contents)
    return git(root, "hash-object", "-w", str(path)), hashlib.sha256(contents).hexdigest()


def tree(root: Path, entries: list[tuple[str, str, str]]) -> str:
    """Create a Git tree from (mode, object-id, safe-name) fixture entries."""
    listing = "".join(f"{mode} {('tree' if mode == '040000' else 'blob')} {object_id}\t{name}\n" for mode, object_id, name in entries).encode("utf-8")
    result = subprocess.run(
        ["git", "mktree"], cwd=root, input=listing, capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(f"git mktree failed: {result.stderr.decode('utf-8', errors='replace')}")
    return result.stdout.decode("ascii").strip()


def sealed_evidence(root: Path, *, head: str, tree: str, report_id: str,
                    mutation=None) -> dict:
    """Create the minimum *real blob-bound* package-audit execution evidence.

    This intentionally models the protocol, rather than a loose audit summary:
    the evidence manifest and every raw transcript are independently content
    addressed Git blobs.  Negative tests mutate this value before it is sealed.
    """
    transcript = (
        "case=PKG-AUDIT-FIXTURE\n"
        "command=python scripts/validate_package_release.py --skill-root .\n"
        "exitCode=0\n"
        "total=1 passed=1 failed=0 skipped=0\n"
    ).encode("utf-8")
    transcript_blob, transcript_sha = blob(root, "audit-transcript.txt", transcript)
    artifact = b'{"status":"PASS","source":"fixture"}\n'
    artifact_blob, artifact_sha = blob(root, "audit-result.json", artifact)
    value = {
        "schemaVersion": "3.2",
        "manifestType": "vibe-control-package-audit-evidence",
        "manifestId": f"EVIDENCE-{report_id}",
        "reportId": report_id,
        "version": (root / "VERSION").read_text(encoding="utf-8").strip(),
        "candidateCommit": head,
        "candidateTree": tree,
        "packageManifestSha256": sha(root / "package-manifest.json"),
        "runtimeManifestSha256": sha(root / "assets" / "project-control" / "runtime" / "runtime-manifest.json"),
        "assuranceMatrixSha256": sha(root / "references" / "controller-assurance-matrix.json"),
        "auditor": {"actorId": "external-auditor", "sessionId": "audit-session"},
        "auditedAt": "2026-07-26T08:00:02+00:00",
        "counters": {"executed": 1, "passed": 1, "failed": 0, "skipped": 0},
        "cases": [{
            "caseId": "PKG-AUDIT-FIXTURE",
            "command": [sys.executable, "scripts/validate_package_release.py", "--skill-root", "."],
            "startedAt": "2026-07-26T08:00:00+00:00",
            "finishedAt": "2026-07-26T08:00:01+00:00",
            "exitCode": 0,
            "status": "PASS",
            "counters": {"executed": 1, "passed": 1, "failed": 0, "skipped": 0},
            "transcriptBlob": transcript_blob,
            "transcriptSha256": transcript_sha,
            "transcriptBytes": len(transcript),
            "transcriptPath": "transcripts/PKG-AUDIT-FIXTURE.txt",
            "validatedControlIds": CONTROL_IDS,
            "artifacts": [{
                "path": "artifacts/PKG-AUDIT-FIXTURE.json",
                "blob": artifact_blob,
                "sha256": artifact_sha,
                "bytes": len(artifact),
            }],
        }],
    }
    if mutation:
        mutation(value)
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    evidence_blob, evidence_sha = blob(root, "audit-evidence-manifest.json", data)
    return {
        "evidenceManifestBlob": evidence_blob,
        "evidenceManifestSha256": evidence_sha,
        "transcriptBlob": transcript_blob,
        "artifactBlob": artifact_blob,
        "evidence": value,
    }


def package_report(root: Path, expect: int | None = None) -> dict:
    result = run(root, sys.executable, str(root / "scripts" / "validate_package_release.py"), "--skill-root", str(root), expect=expect)
    return json.loads(result.stdout)


def package_copy() -> tuple[tempfile.TemporaryDirectory, Path]:
    temp = tempfile.TemporaryDirectory(prefix="vibe-control-package-audit-", ignore_cleanup_errors=True)
    target = Path(temp.name) / "skill"
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns(".git", ".vibe-control", "__pycache__", "*.pyc"))
    git(target, "init")
    git(target, "config", "user.email", "fixture@example.invalid")
    git(target, "config", "user.name", "Fixture")
    matrix_path = target / "references" / "controller-assurance-matrix.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    for item in matrix.get("confirmedControls", []):
        if item.get("id") in {
            "CTRL-CONFIRMED-020", "CTRL-CONFIRMED-021", "CTRL-CONFIRMED-022",
            "CTRL-CONFIRMED-025", "CTRL-CONFIRMED-026", "CTRL-CONFIRMED-027",
        }:
            item["independentValidation"] = "PASS"
    write_json(matrix_path, matrix)
    # The installed 0.3.4 tree is intentionally DEVELOPMENT_DIAGNOSTIC. This
    # isolated fixture models a future sealed candidate so the release-seal
    # validator remains directly testable without changing the installed tree.
    builder_path = target / "scripts" / "build_manifest.py"
    builder_text = builder_path.read_text(encoding="utf-8")
    builder_text = builder_text.replace('"maturity": "DEVELOPMENT_DIAGNOSTIC"', '"maturity": "AWAITING_EXTERNAL_VALIDATION"')
    builder_path.write_text(builder_text, encoding="utf-8", newline="\n")
    run(target, sys.executable, str(target / "scripts" / "build_manifest.py"), "--root", str(target))
    git(target, "add", "-A")
    git(target, "commit", "-m", "candidate")
    return temp, target


def seal(root: Path, *, result: str = "PASS", counts: dict | None = None, same_identity: bool = False,
         receipt_mutation=None, report_mutation=None, evidence_mutation=None,
         omit_evidence: bool = False, lightweight_release: bool = False) -> dict:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    head = git(root, "rev-parse", "HEAD")
    candidate_tree = git(root, "show", "-s", "--format=%T", "HEAD")
    finding_counts = counts or {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    auditor = {"actorId": "external-auditor", "sessionId": "audit-session"}
    implementer = {"actorId": "external-auditor" if same_identity else "implementer", "sessionId": "audit-session" if same_identity else "implementation-session"}
    report_id = f"AUDIT-{version}"
    evidence = None if omit_evidence else sealed_evidence(
        root, head=head, tree=candidate_tree, report_id=report_id, mutation=evidence_mutation,
    )
    report = {
        "schemaVersion": "3.2", "reportType": "vibe-control-package-audit", "reportId": report_id,
        "version": version, "candidateCommit": head, "candidateTree": candidate_tree,
        "packageManifestSha256": sha(root / "package-manifest.json"),
        "runtimeManifestSha256": sha(root / "assets" / "project-control" / "runtime" / "runtime-manifest.json"),
        "assuranceMatrixSha256": sha(root / "references" / "controller-assurance-matrix.json"),
        "auditor": auditor, "implementer": implementer, "auditedAt": "2026-07-26T08:00:02+00:00",
        "result": result, "findingCounts": finding_counts, "validatedControlIds": CONTROL_IDS,
    }
    if evidence:
        report.update({key: evidence[key] for key in ("evidenceManifestBlob", "evidenceManifestSha256")})
    else:
        report.update({"evidenceManifestBlob": "0" * 40, "evidenceManifestSha256": "0" * 64})
    if report_mutation:
        report_mutation(report)
    report_path = Path(root.parent) / "audit-report.json"
    report_bytes = write_json(report_path, report)
    report_blob = git(root, "hash-object", "-w", str(report_path))
    if evidence:
        transcripts_tree = tree(root, [("100644", evidence["transcriptBlob"], "PKG-AUDIT-FIXTURE.txt")])
        artifacts_tree = tree(root, [("100644", evidence["artifactBlob"], "PKG-AUDIT-FIXTURE.json")])
        audit_bundle_tree = tree(root, [
            ("100644", report_blob, "report.json"),
            ("100644", evidence["evidenceManifestBlob"], "evidence-manifest.json"),
            ("040000", artifacts_tree, "artifacts"),
            ("040000", transcripts_tree, "transcripts"),
        ])
    else:
        # Summary-only is deliberately sealed as a syntactically valid but
        # evidence-empty bundle, so the evidence-manifest rule—not tag shape—
        # is the expected failure.
        audit_bundle_tree = tree(root, [("100644", report_blob, "report.json")])
    audit_tag = f"vibe-control-audit/v{version}"
    git(root, "tag", "-a", audit_tag, audit_bundle_tree, "-m", json.dumps({"reportId": report["reportId"], "result": report["result"]}, separators=(",", ":")))
    audit_tag_object = git(root, "rev-parse", f"refs/tags/{audit_tag}")
    receipt = {
        "schemaVersion": "3.2", "receiptType": "vibe-control-package-audit", "releaseTag": f"v{version}",
        "auditTag": audit_tag, "version": version, "candidateCommit": head, "candidateTree": candidate_tree,
        "packageManifestSha256": sha(root / "package-manifest.json"),
        "runtimeManifestSha256": sha(root / "assets" / "project-control" / "runtime" / "runtime-manifest.json"),
        "assuranceMatrixSha256": sha(root / "references" / "controller-assurance-matrix.json"),
        "auditTagObject": audit_tag_object, "auditBundleTree": audit_bundle_tree, "auditReportBlob": report_blob,
        "auditReportSha256": hashlib.sha256(report_bytes).hexdigest(), "auditor": auditor, "implementer": implementer,
        "auditedAt": report["auditedAt"], "result": result, "findingCounts": finding_counts,
        "validatedControlIds": CONTROL_IDS, "enableFormalClaims": True,
    }
    if evidence:
        receipt.update({key: evidence[key] for key in ("evidenceManifestBlob", "evidenceManifestSha256")})
    else:
        receipt.update({"evidenceManifestBlob": "0" * 40, "evidenceManifestSha256": "0" * 64})
    if receipt_mutation:
        receipt_mutation(receipt)
    receipt_path = Path(root.parent) / "package-audit-receipt.json"
    write_json(receipt_path, receipt)
    if lightweight_release:
        git(root, "tag", f"v{version}", head)
    else:
        git(root, "tag", "-a", f"v{version}", head, "-F", str(receipt_path))
    return receipt


def assert_fails(root: Path, expected_id: str) -> dict:
    report = package_report(root, expect=None)
    if report.get("formalClaimsAllowed") is not False or report.get("status") == "PASS":
        raise AssertionError(f"mutation remained formally eligible: {report}")
    ids = {item["id"] for item in report.get("checks", []) if item.get("status") != "PASS"}
    if expected_id not in ids:
        raise AssertionError(f"expected {expected_id}, got {sorted(ids)}")
    return report


def test_valid_package_release_seal() -> None:
    temp, root = package_copy()
    try:
        seal(root)
        report = package_report(root, expect=0)
        if report.get("readiness") != "FORMAL_GATE_READY" or report.get("formalClaimsAllowed") is not True:
            raise AssertionError(f"valid seal was rejected: {report}")
    finally:
        temp.cleanup()


def test_post_audit_runtime_change_invalidates() -> None:
    temp, root = package_copy()
    try:
        seal(root)
        path = root / "assets" / "project-control" / "runtime" / "vibe_runtime" / "common.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# post-audit mutation\n", encoding="utf-8", newline="\n")
        git(root, "add", str(path.relative_to(root)))
        git(root, "commit", "-m", "mutate runtime after audit")
        assert_fails(root, "RUNTIME-MANIFEST-VERIFY")
    finally:
        temp.cleanup()


def test_post_audit_test_change_invalidates() -> None:
    temp, root = package_copy()
    try:
        seal(root)
        path = root / "scripts" / "test_assurance_harness.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# post-audit mutation\n", encoding="utf-8", newline="\n")
        git(root, "add", str(path.relative_to(root)))
        git(root, "commit", "-m", "mutate test after audit")
        assert_fails(root, "PKG-MANIFEST-VERIFY")
    finally:
        temp.cleanup()


def test_stale_runtime_manifest_cannot_be_resealed() -> None:
    """A fresh tag/receipt must not bless a new HEAD with an old runtime inventory."""
    temp, root = package_copy()
    try:
        path = root / "assets" / "project-control" / "runtime" / "vibe_runtime" / "common.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# stale runtime manifest fixture\n", encoding="utf-8", newline="\n")
        git(root, "add", str(path.relative_to(root)))
        git(root, "commit", "-m", "mutate runtime before reseal")
        # Seal only after the mutation: candidate/tag/worktree checks must all pass,
        # leaving the runtime-manifest verifier as the sole expected red light.
        seal(root)
        assert_fails(root, "RUNTIME-MANIFEST-VERIFY")
    finally:
        temp.cleanup()


def test_post_audit_excluded_control_change_invalidates_candidate() -> None:
    """Even excluded dogfood records cannot move HEAD beyond the audited release tag."""
    temp, root = package_copy()
    try:
        seal(root)
        path = root / ".vibe-control" / "post-audit-probe.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8", newline="\n")
        git(root, "add", str(path.relative_to(root)))
        git(root, "commit", "-m", "mutate excluded control plane after audit")
        assert_fails(root, "PKG-AUDIT-CANDIDATE")
    finally:
        temp.cleanup()


def test_stale_package_manifest_cannot_be_resealed() -> None:
    """A fresh tag/receipt must not bless a new HEAD with an old package inventory."""
    temp, root = package_copy()
    try:
        path = root / "scripts" / "test_assurance_harness.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# stale package manifest fixture\n", encoding="utf-8", newline="\n")
        git(root, "add", str(path.relative_to(root)))
        git(root, "commit", "-m", "mutate package before reseal")
        # As above, this is deliberately a correctly bound new seal around
        # stale bytes, not a post-seal candidate-drift mutation.
        seal(root)
        assert_fails(root, "PKG-MANIFEST-VERIFY")
    finally:
        temp.cleanup()


def test_audit_evidence_closure_negative_mutations() -> None:
    """A report summary alone must never substitute for executable audit evidence."""
    mutations = [
        ("summary-only", {"omit_evidence": True}, "PKG-AUDIT-EVIDENCE-MANIFEST"),
        (
            "missing-transcript",
            {"evidence_mutation": lambda value: value["cases"][0].pop("transcriptBlob")},
            "PKG-AUDIT-EVIDENCE-TRANSCRIPT",
        ),
        (
            "wrong-transcript-hash",
            {"evidence_mutation": lambda value: value["cases"][0].update({"transcriptSha256": "0" * 64})},
            "PKG-AUDIT-EVIDENCE-TRANSCRIPT",
        ),
        (
            "zero-counters",
            {"evidence_mutation": lambda value: value["cases"][0].update({"counters": {"executed": 0, "passed": 0, "failed": 0, "skipped": 0}})},
            "PKG-AUDIT-EVIDENCE-COUNTERS",
        ),
        (
            "skip-counter",
            {"evidence_mutation": lambda value: value["cases"][0].update({"counters": {"executed": 1, "passed": 0, "failed": 0, "skipped": 1}})},
            "PKG-AUDIT-EVIDENCE-COUNTERS",
        ),
        (
            "wrong-evidence-candidate",
            {"evidence_mutation": lambda value: value.update({"candidateCommit": "f" * 40})},
            "PKG-AUDIT-EVIDENCE-BINDING",
        ),
        (
            "wrong-evidence-manifest-hash",
            {"report_mutation": lambda value: value.update({"evidenceManifestSha256": "e" * 64})},
            "PKG-AUDIT-EVIDENCE-BINDING",
        ),
        (
            "aggregate-counter-drift",
            {"evidence_mutation": lambda value: value.update({"counters": {"executed": 2, "passed": 2, "failed": 0, "skipped": 0}})},
            "PKG-AUDIT-EVIDENCE-COUNTERS",
        ),
        (
            "incomplete-control-coverage",
            {"evidence_mutation": lambda value: value["cases"][0].update({"validatedControlIds": CONTROL_IDS[:-1]})},
            "PKG-AUDIT-EVIDENCE-COVERAGE",
        ),
        (
            "unknown-control-coverage",
            {"evidence_mutation": lambda value: value["cases"][0].update({"validatedControlIds": [*CONTROL_IDS, "CTRL-UNKNOWN-999"]})},
            "PKG-AUDIT-EVIDENCE-COVERAGE",
        ),
        (
            "wrong-artifact-hash",
            {"evidence_mutation": lambda value: value["cases"][0]["artifacts"][0].update({"sha256": "a" * 64})},
            "PKG-AUDIT-EVIDENCE-ARTIFACT",
        ),
        (
            "empty-artifacts",
            {"evidence_mutation": lambda value: value["cases"][0].update({"artifacts": []})},
            "PKG-AUDIT-EVIDENCE-ARTIFACT",
        ),
    ]
    for name, options, expected in mutations:
        temp, root = package_copy()
        try:
            # Every fixture creates an annotated audit tag and release tag for
            # the current clean HEAD.  These tests may fail only on the named
            # evidence rule, never by omitting an upstream seal prerequisite.
            seal(root, **options)
            assert_fails(root, expected)
        except Exception as exc:
            raise AssertionError(f"{name}: {exc}") from exc
        finally:
            temp.cleanup()


def test_package_release_negative_mutations() -> None:
    mutations = [
        ("missing-tags", None, "PKG-AUDIT-RELEASE-TAG-MISSING"),
        ("lightweight", {"lightweight_release": True}, "PKG-AUDIT-RELEASE-TAG-ANNOTATED"),
        ("wrong-package-hash", {"receipt_mutation": lambda value: value.update({"packageManifestSha256": "0" * 64})}, "PKG-AUDIT-RECEIPT-BINDING"),
        ("wrong-runtime-hash", {"receipt_mutation": lambda value: value.update({"runtimeManifestSha256": "1" * 64})}, "PKG-AUDIT-RECEIPT-BINDING"),
        ("wrong-matrix-hash", {"receipt_mutation": lambda value: value.update({"assuranceMatrixSha256": "2" * 64})}, "PKG-AUDIT-RECEIPT-BINDING"),
        ("same-identity", {"same_identity": True}, "PKG-AUDIT-INDEPENDENCE"),
        ("non-pass", {"result": "FAIL"}, "PKG-AUDIT-RESULT"),
        ("open-p0", {"counts": {"P0": 1, "P1": 0, "P2": 0, "P3": 0}}, "PKG-AUDIT-RESULT"),
        ("open-p1", {"counts": {"P0": 0, "P1": 1, "P2": 0, "P3": 0}}, "PKG-AUDIT-RESULT"),
        ("report-substitution", {"receipt_mutation": lambda value: value.update({"auditReportSha256": "3" * 64})}, "PKG-AUDIT-REPORT-HASH"),
    ]
    for name, options, expected in mutations:
        temp, root = package_copy()
        try:
            if options is not None:
                seal(root, **options)
            assert_fails(root, expected)
        except Exception as exc:
            raise AssertionError(f"{name}: {exc}") from exc
        finally:
            temp.cleanup()

    temp, root = package_copy()
    try:
        seal(root)
        (root / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        assert_fails(root, "PKG-AUDIT-WORKTREE-CLEAN")
    finally:
        temp.cleanup()

    with tempfile.TemporaryDirectory(prefix="vibe-control-no-git-", ignore_cleanup_errors=True) as temp_name:
        root = Path(temp_name) / "skill"
        shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", ".vibe-control", "__pycache__", "*.pyc"))
        assert_fails(root, "PKG-AUDIT-GIT")


TESTS = [
    test_valid_package_release_seal,
    test_post_audit_runtime_change_invalidates,
    test_post_audit_test_change_invalidates,
    test_post_audit_excluded_control_change_invalidates_candidate,
    test_stale_runtime_manifest_cannot_be_resealed,
    test_stale_package_manifest_cannot_be_resealed,
    test_audit_evidence_closure_negative_mutations,
    test_package_release_negative_mutations,
]


def main() -> int:
    results = []
    for test in TESTS:
        try:
            test()
            results.append({"case": test.__name__, "status": "PASS"})
        except Exception as exc:
            results.append({"case": test.__name__, "status": "FAIL", "error": str(exc)})
    passed = sum(item["status"] == "PASS" for item in results)
    report = {"test": "package-release-audit", "status": "PASS" if passed == len(results) else "FAIL", "counters": {"total": len(results), "passed": passed, "failed": len(results) - passed}, "cases": results}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
