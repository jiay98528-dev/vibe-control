from __future__ import annotations

import base64
import hashlib
import contextlib
import importlib
import io
import json
import subprocess
import sys
import tempfile
import os
import shutil
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SOURCE_SKILL_ROOT = Path(__file__).resolve().parents[1]
SHARED_TEST_PACKAGE_DESCRIPTOR_ARG = "--shared-package-descriptor"
SHARED_TEST_PACKAGE_DESCRIPTOR_SHA_ARG = "--shared-package-descriptor-sha256"
_PROJECTION_EXCLUDES = {
    "package-manifest.json",
    "references/controller-assurance-matrix.json",
    "scripts/build_manifest.py",
}


class _BorrowedTestPackage:
    """Match TemporaryDirectory.cleanup without owning the shared fixture."""

    def cleanup(self) -> None:
        return None


def _released_test_package() -> tuple[tempfile.TemporaryDirectory, Path]:
    """Create and fully validate one real, locally tagged package fixture."""
    import test_package_release_audit as package_fixture
    temp, root = package_fixture.package_copy()
    package_fixture.seal(root)
    report = package_fixture.package_report(root, expect=0)
    if report.get("formalClaimsAllowed") is not True:
        temp.cleanup()
        raise AssertionError(f"synthetic package release seal failed: {report}")
    return temp, root


def _argv_value(flag: str) -> str | None:
    try:
        index = sys.argv.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(sys.argv):
        raise AssertionError(f"{flag} requires a value")
    return sys.argv[index + 1]


def _git_value(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode:
        raise AssertionError(f"cannot inspect shared test package: {result.stderr.strip()}")
    return result.stdout.strip()


def _package_identity(root: Path) -> dict:
    return {
        "head": _git_value(root, "rev-parse", "HEAD"),
        "tree": _git_value(root, "show", "-s", "--format=%T", "HEAD"),
        "packageManifestSha256": hashlib.sha256((root / "package-manifest.json").read_bytes()).hexdigest(),
        "runtimeManifestSha256": hashlib.sha256((root / "assets" / "project-control" / "runtime" / "runtime-manifest.json").read_bytes()).hexdigest(),
        "assuranceMatrixSha256": hashlib.sha256((root / "references" / "controller-assurance-matrix.json").read_bytes()).hexdigest(),
    }


def _project_identity(root: Path) -> dict:
    return {
        "head": _git_value(root, "rev-parse", "HEAD"),
        "tree": _git_value(root, "show", "-s", "--format=%T", "HEAD"),
        "status": _git_value(root, "status", "--porcelain=v1"),
    }


def _verify_project_runtime_inventory(root: Path) -> str:
    runtime_version = (SOURCE_SKILL_ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
    runtime_root = root / ".vibe-control" / "runtime" / runtime_version
    manifest_path = runtime_root / "runtime-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise AssertionError("project runtime manifest has no file inventory")
    for item in files:
        relative = item.get("path")
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise AssertionError("project runtime manifest contains an unsafe path")
        path = runtime_root / Path(relative)
        if (
            not path.is_file()
            or path.stat().st_size != item.get("bytes")
            or hashlib.sha256(path.read_bytes()).hexdigest() != item.get("sha256")
        ):
            raise AssertionError(f"project runtime inventory drifted: {relative}")
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def _source_projection(root: Path) -> str:
    """Hash every copied source byte except the three intentional seal transforms."""
    digest = hashlib.sha256()
    entries: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        parts = set(relative.parts)
        relative_text = relative.as_posix()
        if ".git" in parts or ".vibe-control" in parts or "__pycache__" in parts:
            continue
        if path.suffix == ".pyc" or relative_text in _PROJECTION_EXCLUDES:
            continue
        entries.append(path)
    for path in sorted(entries, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def _validate_shared_descriptor(path: Path, expected_sha256: str, *, require_parent: bool) -> tuple[dict, Path]:
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise AssertionError("shared test package descriptor hash drifted")
    try:
        descriptor = json.loads(data)
    except json.JSONDecodeError as exc:
        raise AssertionError("shared test package descriptor is malformed") from exc
    if descriptor.get("schemaVersion") != "1.0" or descriptor.get("parentValidated") is not True:
        raise AssertionError("shared test package descriptor lacks parent validation")
    if require_parent and descriptor.get("parentPid") != os.getppid():
        raise AssertionError("shared test package descriptor is not owned by this worker parent")
    root = Path(descriptor.get("packageRoot", "")).resolve()
    if not root.is_dir():
        raise AssertionError(f"shared released test package is missing: {root}")
    source_projection = _source_projection(SOURCE_SKILL_ROOT)
    package_projection = _source_projection(root)
    if source_projection != descriptor.get("sourceProjectionSha256") or package_projection != source_projection:
        raise AssertionError("shared released test package differs from the current test source")
    actual = _package_identity(root)
    if actual != descriptor.get("packageIdentity"):
        raise AssertionError(f"shared released test package identity drifted: {actual}")
    status = _git_value(root, "status", "--porcelain=v1")
    version = (SOURCE_SKILL_ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
    if status:
        raise AssertionError("shared released test package is dirty")
    if (root / "VERSION").read_text(encoding="utf-8-sig").strip() != version:
        raise AssertionError("shared released test package version differs from the test source")
    for tag in (f"v{version}", f"vibe-control-audit/v{version}"):
        if _git_value(root, "cat-file", "-t", f"refs/tags/{tag}") != "tag":
            raise AssertionError(f"shared released test package tag is not annotated: {tag}")
    baseline = descriptor.get("defaultProject")
    if not isinstance(baseline, dict):
        raise AssertionError("shared test package descriptor lacks the default project fixture")
    baseline_root = Path(baseline.get("root", "")).resolve()
    if not baseline_root.is_dir() or _project_identity(baseline_root) != baseline.get("identity"):
        raise AssertionError("shared default project fixture identity drifted")
    baseline_runtime = baseline_root / ".vibe-control" / "runtime" / version / "runtime-manifest.json"
    if hashlib.sha256(baseline_runtime.read_bytes()).hexdigest() != actual["runtimeManifestSha256"]:
        raise AssertionError("shared default project fixture uses a different runtime")
    if _verify_project_runtime_inventory(baseline_root) != baseline.get("runtimeManifestSha256"):
        raise AssertionError("shared default project fixture runtime inventory is not bound")
    if baseline.get("runtimeSubprocessSentinel") != "PASS":
        raise AssertionError("shared default project fixture lacks a real runtime subprocess sentinel")
    return descriptor, root


def write_shared_test_package_descriptor(path: Path) -> tuple[Path, str]:
    """Write a parent-owned descriptor after this process performed the full seal validation."""
    _prepare_shared_default_project()
    descriptor = {
        "schemaVersion": "1.0",
        "parentPid": os.getpid(),
        "parentValidated": True,
        "packageRoot": str(SKILL_ROOT.resolve()),
        "packageIdentity": _package_identity(SKILL_ROOT),
        "sourceProjectionSha256": _source_projection(SOURCE_SKILL_ROOT),
        "defaultProject": {
            "root": str(_SHARED_DEFAULT_PROJECT_ROOT.resolve()),
            "identity": _project_identity(_SHARED_DEFAULT_PROJECT_ROOT),
            "runtimeManifestSha256": _verify_project_runtime_inventory(_SHARED_DEFAULT_PROJECT_ROOT),
            "runtimeSubprocessSentinel": _SHARED_DEFAULT_RUNTIME_SENTINEL,
            "privateKeys": {
                name: base64.b64encode(key.private_bytes(
                    serialization.Encoding.Raw,
                    serialization.PrivateFormat.Raw,
                    serialization.NoEncryption(),
                )).decode("ascii")
                for name, key in _SHARED_DEFAULT_KEYS.items()
            },
        },
    }
    if _source_projection(SKILL_ROOT) != descriptor["sourceProjectionSha256"]:
        raise AssertionError("parent package fixture is not derived from the current test source")
    data = (json.dumps(descriptor, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    path.write_bytes(data)
    path.chmod(0o444)
    return path.resolve(), hashlib.sha256(data).hexdigest()


def verify_shared_test_package_unchanged(path: Path, expected_sha256: str) -> None:
    _validate_shared_descriptor(path, expected_sha256, require_parent=False)


_descriptor_value = _argv_value(SHARED_TEST_PACKAGE_DESCRIPTOR_ARG)
_descriptor_sha = _argv_value(SHARED_TEST_PACKAGE_DESCRIPTOR_SHA_ARG)
if bool(_descriptor_value) != bool(_descriptor_sha):
    raise AssertionError("shared test package descriptor arguments must be supplied together")
if _descriptor_value and _descriptor_sha:
    _SHARED_DESCRIPTOR, SKILL_ROOT = _validate_shared_descriptor(
        Path(_descriptor_value).resolve(), _descriptor_sha, require_parent=True
    )
    _TEST_PACKAGE_TEMP: tempfile.TemporaryDirectory | _BorrowedTestPackage = _BorrowedTestPackage()
else:
    _SHARED_DESCRIPTOR = None
    _TEST_PACKAGE_TEMP, SKILL_ROOT = _released_test_package()
WRAPPER = SKILL_ROOT / "scripts" / "vibe_control.py"
_SHARED_DEFAULT_PROJECT_TEMP: tempfile.TemporaryDirectory | None = None
_SHARED_DEFAULT_PROJECT_ROOT: Path | None = None
_SHARED_DEFAULT_KEYS: dict[str, Ed25519PrivateKey] = {}
_SHARED_DEFAULT_RUNTIME_SENTINEL: str | None = None
if _SHARED_DESCRIPTOR:
    _baseline = _SHARED_DESCRIPTOR["defaultProject"]
    _SHARED_DEFAULT_PROJECT_ROOT = Path(_baseline["root"]).resolve()
    _SHARED_DEFAULT_KEYS = {
        name: Ed25519PrivateKey.from_private_bytes(base64.b64decode(value))
        for name, value in _baseline["privateKeys"].items()
    }
_INPROCESS_ASSURANCE_COMMANDS = bool(_SHARED_DESCRIPTOR)


def enable_inprocess_assurance_commands() -> None:
    """Opt in only the bounded assurance parent; other suites keep real CLI processes."""
    global _INPROCESS_ASSURANCE_COMMANDS
    _INPROCESS_ASSURANCE_COMMANDS = True


COMMAND_TIMEOUT_SECONDS = int(os.environ.get("VIBE_CONTROL_TEST_COMMAND_TIMEOUT", "120"))


def run(*args: str, cwd: Path | None = None, expect: int | None = 0) -> subprocess.CompletedProcess[str]:
    try:
        value = subprocess.run(
            list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"ASSURANCE-COMMAND-TIMEOUT after {COMMAND_TIMEOUT_SECONDS}s: {' '.join(args)}"
        ) from exc
    if expect is not None and value.returncode != expect:
        raise AssertionError(f"exit {value.returncode}, expected {expect}\nstdout={value.stdout}\nstderr={value.stderr}")
    return value


def git(root: Path, *args: str) -> str:
    return run("git", "-C", str(root), *args).stdout.strip()


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A"); git(root, "commit", "-m", message); return git(root, "rev-parse", "HEAD")


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load(path: Path): return json.loads(path.read_text(encoding="utf-8-sig"))
def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def ref(root: Path, path: Path) -> dict: return {"path":path.relative_to(root).as_posix(),"bytes":path.stat().st_size,"sha256":sha(path),"tracked":True}
RUNTIME_VERSION = (SOURCE_SKILL_ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def runtime(root: Path) -> Path: return root / ".vibe-control" / "runtime" / RUNTIME_VERSION / "control.py"
def main_evidence_path(root: Path) -> Path: return next(path for path in (root/".vibe-control"/"evidence").glob("*.json") if not path.name.endswith(("attestation.json", "adapter-invocation.json")))


def adapter_binding(adapter_id: str = "generic-command") -> dict:
    catalog = load(SKILL_ROOT / "assets" / "project-control" / "runtime" / "rules" / "v1" / "adapters.json")
    descriptor = next(item for item in catalog["adapters"] if item["id"] == adapter_id)
    return {"id": adapter_id, "version": descriptor["version"], "sha256": hashlib.sha256(json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def source_id(prefix: str, statement: str) -> str:
    normalized = " ".join(statement.strip().split())
    return f"{prefix}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]}"


def positioning_axes(release_intent: str) -> dict:
    gate = "owner acceptance"
    signal = "CASE-001 passes"
    return {"primaryExperience":"SERVICE","capabilityDomains":["BACKEND_API"],"deliveryObjective":"VERTICAL_SLICE","releaseIntent":release_intent,"runtimeTargets":["python-local"],"targetEnvironments":[{"id":"test","operatingSystem":"Windows","deviceClass":"desktop","architecture":"x86_64"}],"distributionChannels":[],"humanQualityGates":[{"id":source_id("HG",gate),"statement":gate}],"nonGoals":[],"firstVerticalSlice":{"outcome":"fixture command succeeds","included":["one command"],"excluded":["deployment"],"successSignals":[{"id":source_id("SIG",signal),"statement":signal}]}}


def task_contract(*, risk: str, task_ceiling: str) -> dict:
    signal_id = source_id("SIG", "CASE-001 passes")
    gate_id = source_id("HG", "owner acceptance")
    automated_claim = "DEVELOPMENT_CHECKED" if task_ceiling == "DEVELOPMENT_CHECKED" else "VERIFIED"
    checkpoints = [{"id":"CP-001","sourceRefs":[signal_id],"objectiveRefs":["KO-001"],"statement":"CASE-001 returns the locked success marker","type":"AUTOMATED","requiredForClaim":automated_claim,"caseIds":["CASE-001"],"assertions":[{"id":"ASRT-001","statement":"CASE-001 exits successfully and prints OK","caseIds":["CASE-001"]}],"expected":{"status":"PASS","minExecuted":1,"maxFailed":0,"maxSkipped":0,"artifacts":"AS_DECLARED"},"notProven":[]}]
    if task_ceiling in {"ACCEPTED", "RELEASE_READY"}:
        checkpoints.append({"id":"CP-002","sourceRefs":[gate_id],"objectiveRefs":["KO-001"],"statement":"owner accepts the fixture outcome","type":"HUMAN","requiredForClaim":"ACCEPTED","caseIds":[],"assertions":[],"expected":{"status":"PASS","minExecuted":1,"maxFailed":0,"maxSkipped":0,"artifacts":"AS_DECLARED"},"notProven":["automatic product judgment"]})
    policy={"strategy":"PROJECT_DERIVED","maxExploratoryFindings":3,"stopCondition":"ALL_REQUIRED_CHECKPOINTS_REPORTED","requiredReviewRoles":["INDEPENDENT_AUDITOR"],"triggerReasons":["MILESTONE_CANDIDATE_READY"]}
    payload={"acceptanceCheckpoints":checkpoints,"auditPolicy":policy}
    checkpoint_hash=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    checkpoint_ids=[item["id"] for item in checkpoints]
    planning={
        "milestones":[{"id":"MS-001","outcome":"close the observable fixture outcome","objectiveRefs":["KO-001"],"dependsOn":[],"workNodes":[{"id":"WN-001","title":"implement and check the fixture","kind":"IMPLEMENTATION","allowedPaths":["fixture.py"],"minimumChecks":["QC-001","CASE-001"],"ownerRole":"IMPLEMENTER"}],"checkpointIds":checkpoint_ids,"expectedPassConditions":["all fixture checkpoints report PASS"]}],
        "scorecardPlan":{"weights":{"FUNCTIONALITY":40,"ROBUSTNESS_SECURITY":25,"AUDIT":20,"PROCESS":15},"items":[
            {"id":"SC-001","category":"FUNCTIONALITY","statement":"fixture behavior works","checkpointIds":checkpoint_ids,"factSources":[{"kind":"CHECKPOINT","refs":checkpoint_ids}]},
            {"id":"SC-002","category":"ROBUSTNESS_SECURITY","statement":"fixture failure is conserved","checkpointIds":checkpoint_ids,"factSources":[{"kind":"CASE","refs":["CASE-001"]}]},
            {"id":"SC-003","category":"AUDIT","statement":"fixture evidence receives fresh review","checkpointIds":checkpoint_ids,"factSources":[{"kind":"REVIEW","refs":["FRESH_INDEPENDENT_REVIEW"]}]},
            {"id":"SC-004","category":"PROCESS","statement":"fixture keeps the minimum proof boundary","checkpointIds":checkpoint_ids,"factSources":[{"kind":"CORE_CONTROL","refs":["RULE-CORE-OBSERVABLE-CANDIDATE"]}]},
        ]},
        "verificationStrategy":{"mode":"CANDIDATE_BOUND","failureDisposition":"REPAIR_WITHIN_CONTRACT","eligibleObservations":["runtime-observed","blackbox-observed"],"requireZeroSkipped":True,"checkpointCases":[{"checkpointId":"CP-001","caseIds":["CASE-001"]}],"implementer":{"quickChecks":[{"id":"QC-001","command":[sys.executable,"-m","py_compile","fixture.py"],"requiredBeforeMilestone":True}]},"executor":{"caseIds":["CASE-001"],"evidenceRequirements":["candidate-bound transcript","nonzero counters","zero skipped cases"]},"auditor":{"required":True,"form":"FRESH_INDEPENDENT_REVIEW","inputs":["candidate","case evidence","checkpoint expectations"],"stopCondition":"ALL_REQUIRED_CHECKPOINTS_REPORTED"},"notProven":["external distribution"]},
        "guardPolicy":{"defaultEffect":"ADVISORY","guards":[{"id":"GUARD-ACTION","scope":"MUTATION","effect":"ACTION_GUARD"},{"id":"GUARD-CLAIM","scope":"CLAIM","effect":"CLAIM_GUARD"},{"id":"GUARD-PROCESS","scope":"PROCESS","effect":"ADVISORY"},{"id":"GUARD-HUMAN","scope":"HUMAN","effect":"HUMAN_DECISION"},{"id":"GUARD-ENVIRONMENT","scope":"ENVIRONMENT","effect":"ENVIRONMENT_BLOCKED"}]},
        "reportingPolicy":{"orientation":"ZERO_CONTEXT_ORIENTATION","progressMode":"NON_BLOCKING","reviewPoint":"OWNER_REVIEW","plainLanguageFields":["projectPurpose","whatWasDone","whatWorksNow","whatStillDoesNotWork","userImpact","canContinue","canRelease"],"nextActions":{"continue":["continue the locked fixture work"],"repair":["repair the failed fixture check"],"humanReview":["review the fixture candidate"]}},
    }
    execution_hash=hashlib.sha256(json.dumps(planning,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return {"schemaVersion":"4.0","taskId":"TASK-001","goal":"prove fixture","objectiveRefs":["KO-001","KF-001"],"allowedPaths":["fixture.py"],"forbiddenPaths":["forbidden.txt","secrets/**"],"requiredCaseIds":["CASE-001"],"risk":risk,"maxClaimLevel":task_ceiling,"authorityRefs":["PROJECT_BRIEF.md"],"nonGoals":[],"humanDecisionPoints":["release"],"acceptanceCheckpoints":checkpoints,"checkpointConfirmation":{"actorId":"owner","summary":"fixture checkpoints confirmed","checkpointSetSha256":checkpoint_hash,"executionPlanSha256":execution_hash,"record":"CHECKPOINT_CONFIRMATION.json","confirmedAt":"2026-07-25T00:00:00+08:00"},"auditPolicy":policy,**planning}


def objective_spec() -> dict:
    summary = "fixture objectives confirmed"
    return {
        "document": "KEY_OBJECTIVES.md", "documentId": "FIXTURE-OBJECTIVES", "revision": 1, "status": "CONFIRMED",
        "sourceDocuments": ["PROJECT_BRIEF.md"], "objectiveIds": ["KO-001"], "failureModeIds": ["KF-001"], "nonGoalIds": ["NG-001"],
        "confirmation": {"actorId": "owner", "summary": summary, "summarySha256": hashlib.sha256(summary.encode()).hexdigest(), "record": "OBJECTIVES_CONFIRMATION.json"},
    }


def automation_policy(project_id: str, *, mode: str = "AUTO_LOCAL_TO_REVIEW") -> dict:
    stop_conditions = sorted([
        "AUTOMATED_CHECKPOINTS_COMPLETE", "HUMAN_CHECKPOINT", "OWNER_DECISION",
        "BOUNDARY_CHANGE", "R3_OR_IRREVERSIBLE_ACTION", "HARD_FAILURE",
        "PUSH_CONFLICT", "USER_INTERRUPT",
    ])
    semantic = {
        "projectId": project_id,
        "mode": mode,
        "commitPolicy": "MANUAL" if mode == "MANUAL_STAGE_CONFIRMATION" else "MILESTONE_COMMITS",
        "pushPolicy": "NONE",
        "stopConditions": stop_conditions,
    }
    summary = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
    return {
        "schemaVersion": "1.0", "policyId": f"automation-{digest[:12]}", **semantic,
        "confirmation": {"actorId": "owner", "summary": summary, "summarySha256": digest,
                         "record": "AUTOMATION_CONFIRMATION.json", "confirmedAt": "2026-07-29T00:00:00+08:00"},
    }


def write_objective_files(root: Path) -> None:
    (root / "KEY_OBJECTIVES.md").write_text("# Fixture objectives\n\n- `KO-001`: prove the fixture outcome\n- `KF-001`: prevent false evidence\n- `NG-001`: no deployment\n", encoding="utf-8", newline="\n")
    write(root / "OBJECTIVES_CONFIRMATION.json", {"actorId": "owner", "decision": "CONFIRM"})


def valid_bootstrap_spec(*, project_id: str = "fixture", release_intent: str = "PRIVATE_OPERATION", trusted_keys: list[dict] | None = None) -> dict:
    """Return a minimal but complete Schema 4.0 bootstrap input for mutation tests."""
    positioning = positioning_axes(release_intent)
    summary_hash = hashlib.sha256(json.dumps(positioning, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"schemaVersion":"4.0","projectId":project_id,**positioning,"confirmation":{"actorId":"owner","summary":"fixture positioning","summarySha256":summary_hash,"record":"POSITIONING_CONFIRMATION.json"},"keyObjectives":objective_spec(),"automationPolicy":automation_policy(project_id),"capabilityProfiles":[],"profileBindings":[],"runtimeAdapters":["generic-command"],"skillBindings":[],"projectOverlay":[],"authorityFiles":["PROJECT_BRIEF.md"],"trustedKeys":trusted_keys or [],"cases":[{"id":"CASE-001","command":[sys.executable,"fixture.py"],"observation":"runtime-observed","maxClaimLevel":"RELEASE_READY" if release_intent == "EXTERNAL_RELEASE" else "ACCEPTED","oracle":{"exitCode":0,"stdoutContainsAll":[],"stderrContainsNone":[]},"artifacts":[],"satisfiesRuleIds":["RULE-CORE-OBSERVABLE-CANDIDATE","RULE-CORE-FAILURE-CONSERVATION","RULE-PROFILE-API-CONTRACT","RULE-ADAPTER-GENERIC_COMMAND"],"capabilities":["candidate-integrity","failure-conservation","api-contract-runtime","generic-command-execution"],"adapter":adapter_binding()}]}


def package_formal_enabled() -> bool:
    result = run(sys.executable, str(SKILL_ROOT / "scripts" / "validate_package_release.py"), "--skill-root", str(SKILL_ROOT), expect=None)
    report = json.loads(result.stdout)
    return result.returncode == 0 and report.get("formalClaimsAllowed") is True


def command(root: Path, *args: str, expect: int | None = 0):
    """Invoke the exact bound runtime in this already process-isolated leaf.

    Bootstrap, installation, package sealing and CLI-surface cases still use
    real subprocesses. Reusing the runtime module here removes repeated Python
    and jsonschema startup without sharing mutable project state across cases.
    """
    if not _INPROCESS_ASSURANCE_COMMANDS:
        result = run(sys.executable, str(runtime(root)), *args, "--project", str(root), expect=expect)
        return result, json.loads(result.stdout)
    runtime_parent = SKILL_ROOT / "assets" / "project-control" / "runtime"
    runtime_parent_text = str(runtime_parent)
    if runtime_parent_text not in sys.path:
        sys.path.insert(0, runtime_parent_text)
    cli = importlib.import_module("vibe_runtime.cli")
    argv = [str(runtime(root)), *args, "--project", str(root)]
    stdout = io.StringIO()
    stderr = io.StringIO()
    previous_argv = sys.argv
    try:
        sys.argv = argv
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            returncode = cli.main()
    finally:
        sys.argv = previous_argv
    result = subprocess.CompletedProcess(argv, returncode, stdout.getvalue(), stderr.getvalue())
    if expect is not None and result.returncode != expect:
        raise AssertionError(
            f"exit {result.returncode}, expected {expect}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result, json.loads(result.stdout)


def public_b64(private: Ed25519PrivateKey) -> str:
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def canonical(value) -> bytes:
    payload = {k:v for k,v in value.items() if k != "signature"} if isinstance(value, dict) else value
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sign(value: dict, private: Ed25519PrivateKey) -> dict:
    value = json.loads(json.dumps(value)); value["signature"] = {"algorithm":"Ed25519","value":base64.b64encode(private.sign(canonical(value))).decode()}; return value


def bootstrap_raw(spec: dict):
    """Create the smallest clean Git project and run bootstrap without assuming success."""
    temp = tempfile.TemporaryDirectory(prefix="vibe-control-bootstrap-")
    base = Path(temp.name); root = base / "project"; root.mkdir()
    git(root, "init"); git(root, "config", "user.email", "fixture@example.invalid"); git(root, "config", "user.name", "Fixture")
    (root / "PROJECT_BRIEF.md").write_text("# Fixture\n", encoding="utf-8")
    write_objective_files(root)
    confirmation = spec.get("confirmation") if isinstance(spec, dict) else None
    if isinstance(confirmation, dict) and isinstance(confirmation.get("record"), str):
        write(root / confirmation["record"], {"actorId": confirmation.get("actorId", "owner"), "decision": "CONFIRM"})
    automation = spec.get("automationPolicy") if isinstance(spec, dict) else None
    if isinstance(automation, dict) and isinstance(automation.get("confirmation", {}).get("record"), str):
        write(root / automation["confirmation"]["record"], {"actorId": automation["confirmation"].get("actorId", "owner"), "decision": "CONFIRM"})
    commit(root, "initial authority")
    spec_path = base / "bootstrap.json"; write(spec_path, spec)
    result = run(sys.executable, str(WRAPPER), "bootstrap", "--project", str(root), "--spec", str(spec_path), expect=None)
    return temp, root, result


def setup_project(*, risk: str = "R2", task_ceiling: str | None = None, case_ceiling: str | None = None, observation: str = "runtime-observed", command_script: str = "fixture.py", ignored_runner_case: bool = False, release_intent: str = "PRIVATE_OPERATION", include_keys: bool = True):
    intent_caps = {"LOCAL_EXPERIMENT":"VERIFIED", "PRIVATE_OPERATION":"ACCEPTED", "EXTERNAL_RELEASE":"RELEASE_READY"}
    task_ceiling = task_ceiling or intent_caps[release_intent]
    case_ceiling = case_ceiling or task_ceiling
    if (
        _SHARED_DEFAULT_PROJECT_ROOT is not None
        and risk == "R2"
        and task_ceiling == "ACCEPTED"
        and case_ceiling == "ACCEPTED"
        and observation == "runtime-observed"
        and command_script == "fixture.py"
        and not ignored_runner_case
        and release_intent == "PRIVATE_OPERATION"
        and include_keys
    ):
        temp = tempfile.TemporaryDirectory(prefix="vibe-control-v3-shared-")
        root = Path(temp.name) / "project"
        shutil.copytree(_SHARED_DEFAULT_PROJECT_ROOT, root)
        if _project_identity(root) != _project_identity(_SHARED_DEFAULT_PROJECT_ROOT):
            temp.cleanup()
            raise AssertionError("copied shared default project fixture drifted")
        return temp, root, dict(_SHARED_DEFAULT_KEYS)
    temp = tempfile.TemporaryDirectory(prefix="vibe-control-v3-"); base = Path(temp.name); root = base / "project"; root.mkdir()
    git(root, "init"); git(root, "config", "user.email", "fixture@example.invalid"); git(root, "config", "user.name", "Fixture")
    (root / "PROJECT_BRIEF.md").write_text("# Fixture\n", encoding="utf-8")
    (root / "POSITIONING_CONFIRMATION.json").write_text('{"actorId":"owner","decision":"CONFIRM"}\n', encoding="utf-8")
    (root / "CHECKPOINT_CONFIRMATION.json").write_text('{"actorId":"owner","decision":"CONFIRM"}\n', encoding="utf-8")
    (root / "AUTOMATION_CONFIRMATION.json").write_text('{"actorId":"owner","decision":"CONFIRM"}\n', encoding="utf-8")
    write_objective_files(root)
    if ignored_runner_case: (root / ".gitignore").write_text("ignored_runner.py\n", encoding="utf-8")
    commit(root, "initial authority")
    keys = {name:Ed25519PrivateKey.generate() for name in ("executor","auditor","release-auditor","owner")} if include_keys else {}
    positioning = positioning_axes(release_intent)
    summary_hash = hashlib.sha256(json.dumps(positioning, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    spec = {"schemaVersion":"4.0","projectId":"fixture",**positioning,"confirmation":{"actorId":"owner","summary":"fixture positioning","summarySha256":summary_hash,"record":"POSITIONING_CONFIRMATION.json"},"keyObjectives":objective_spec(),"automationPolicy":automation_policy("fixture"),"capabilityProfiles":[],"profileBindings":[],"runtimeAdapters":["generic-command"],"skillBindings":[],"projectOverlay":[],"authorityFiles":["PROJECT_BRIEF.md"],"trustedKeys":[{"keyId":f"{name}-key","actorId":name,"role":name,"publicKey":public_b64(key)} for name,key in keys.items()],"cases":[{"id":"CASE-001","command":[sys.executable,command_script],"observation":observation,"maxClaimLevel":case_ceiling,"oracle":{"exitCode":0,"stdoutContainsAll":["OK"],"stderrContainsNone":[]},"artifacts":[],"satisfiesRuleIds":["RULE-CORE-OBSERVABLE-CANDIDATE","RULE-CORE-FAILURE-CONSERVATION","RULE-PROFILE-API-CONTRACT","RULE-ADAPTER-GENERIC_COMMAND"],"capabilities":["candidate-integrity","failure-conservation","api-contract-runtime","generic-command-execution"],"adapter":adapter_binding()}]}
    spec_path = base / "bootstrap.json"; write(spec_path, spec)
    result = run(sys.executable, str(WRAPPER), "bootstrap", "--project", str(root), "--spec", str(spec_path), expect=2); assert json.loads(result.stdout)["status"] == "BLOCKED"; commit(root, "bootstrap v4")
    control = root / ".vibe-control"; runtime_root = control / "runtime" / RUNTIME_VERSION
    contract = task_contract(risk=risk, task_ceiling=task_ceiling)
    contract_path = control / "tasks" / "TASK-001.json"; write(contract_path, contract); commit(root, "add task contract")
    command(root, "lock-task", "--contract", str(contract_path)); commit(root, "lock task")
    (root / "fixture.py").write_text("print('OK')\n", encoding="utf-8"); commit(root, "implement fixture")
    command(root, "freeze", "--actor", "implementer", "--session", "impl-1"); commit(root, "freeze candidate")
    return temp, root, keys


def _prepare_shared_default_project() -> None:
    """Build the common frozen project once; workers only mutate private copies."""
    global _SHARED_DEFAULT_PROJECT_TEMP, _SHARED_DEFAULT_PROJECT_ROOT
    global _SHARED_DEFAULT_KEYS, _SHARED_DEFAULT_RUNTIME_SENTINEL
    if _SHARED_DEFAULT_PROJECT_ROOT is not None:
        return
    temp, root, keys = setup_project()
    _SHARED_DEFAULT_PROJECT_TEMP = temp
    _SHARED_DEFAULT_PROJECT_ROOT = root
    _SHARED_DEFAULT_KEYS = keys
    _verify_project_runtime_inventory(root)
    sentinel = run(
        sys.executable, str(runtime(root)), "inspect", "--project", str(root), expect=0
    )
    sentinel_report = json.loads(sentinel.stdout)
    if sentinel_report.get("status") != "PASS" or sentinel.stderr:
        raise AssertionError(f"project runtime subprocess sentinel failed: {sentinel_report}")
    _SHARED_DEFAULT_RUNTIME_SENTINEL = "PASS"


def execute_and_verify(root: Path):
    command(root, "execute", "--actor", "executor", "--session", "exec-1"); commit(root, "record evidence")
    result, report = command(root, "validate", expect=2); assert report["state"]["derived"]["phase"] == "VERIFIED"; commit(root, "derive verified state")
    _, report = command(root, "validate", expect=2)
    contract = load(root / ".vibe-control" / "tasks" / "TASK-001.json")
    assert report["state"]["declared"]["phase"] == "VERIFIED" and report["formal"]["eligible"] is False
    if contract.get("schemaVersion") == "4.0":
        strategy = contract.get("verificationStrategy", {})
        auditor = strategy.get("auditor", strategy.get("audit", {}))
        policy = contract.get("auditPolicy", {})
        requirement = {
            "required": auditor.get("required") is True,
            "form": auditor.get("form"),
            "roles": list(policy.get("requiredReviewRoles", [])),
            "triggerReasons": list(policy.get("triggerReasons", [])),
        }
        gate_checks = [item for item in report["integrity"]["checks"] if item["id"] == "HC-PROJECT-REVIEW-GATE"]
        assert gate_checks, "Schema 4.0 validation omitted the project-derived review decision"
        gate = gate_checks[-1]
        details = gate.get("details", {})
        assert details.get("reviewForm") == requirement["form"]
        assert set(details.get("roles", [])) == set(requirement["roles"])
        assert set(details.get("triggerReasons", [])) == set(requirement["triggerReasons"])
        if requirement["required"]:
            assert gate["status"] == "BLOCKED" and "HC-PROJECT-REVIEW-GATE" in report["formal"]["blockers"]
        else:
            assert gate["status"] == "PASS" and "HC-PROJECT-REVIEW-GATE" not in report["formal"]["blockers"]
    else:
        # Historical 3.2 fixtures derived review from risk rather than a locked
        # project review form.  Preserve that older assertion only for those
        # archived objects; current fixtures must use the 4.0 branch above.
        if contract.get("risk") in {"R2", "R3"}:
            assert "HC-RISK-REVIEW-GATE" in report["formal"]["blockers"]
    return report


def advance_audit(root: Path, keys: dict, *, same_actor: bool = False, open_high: bool = False, result: str = "PASS", review_id: str = "REVIEW-001"):
    evidence_path = main_evidence_path(root)
    evidence = load(evidence_path)
    transcript = root/".vibe-control"/"reviews"/f"audit-transcript-{review_id}.txt"; transcript.parent.mkdir(parents=True, exist_ok=True); transcript.write_text("independent audit\n", encoding="utf-8"); commit(root, "add audit transcript")
    candidate = load(next((root/".vibe-control"/"candidates").glob("*.json")))
    actor = "executor" if same_actor else "auditor"; session = "exec-1" if same_actor else "audit-1"
    finding = {"id":"F-1","severity":"P1","status":"OPEN","classification":"CURRENT_GOAL_DEFECT","objectiveRefs":["KO-001"],"checkpointRefs":["CP-001"],"coreControlRefs":[],"affectedClaims":["VERIFIED","ACCEPTED","RELEASE_READY"],"reproduction":"fixture mutation","evidenceRefs":[ref(root,evidence_path)],"minimumFix":"fix fixture","addedGovernanceCost":"one regression"}
    review = {"schemaVersion":"4.0","reviewId":review_id,"taskId":"TASK-001","candidateId":candidate["candidateId"],"candidateCommit":candidate["commit"],"checkpointSetSha256":candidate["checkpointSetSha256"],"executionPlanSha256":candidate["executionPlanSha256"],"keyObjectives":candidate["keyObjectives"],"positioning":candidate["positioning"],"resolvedRuleSet":candidate["resolvedRuleSet"],"reviewForm":"FRESH_INDEPENDENT_REVIEW","reviewRoles":["INDEPENDENT_AUDITOR"],"auditor":{"actorId":actor,"sessionId":session},"evidenceIds":[evidence["evidenceId"]],"evidenceRefs":[ref(root,evidence_path)],"checkpointResults":[{"checkpointId":"CP-001","expectedStatus":"PASS","observedStatus":"PASS","evidenceIds":[evidence["evidenceId"]],"deviationFindingId":None}],"findings":[finding] if open_high else [],"transcript":ref(root,transcript),"result":result,"reviewedAt":"2026-07-25T01:00:00+08:00"}
    if "auditor" in keys:
        review.update({"keyId":"auditor-key"}); review=sign(review,keys["auditor"])
    path=Path(root.parent)/"review.json"; write(path,review); result,report=command(root,"audit","--review",str(path),expect=None); return result,report


def advance_accept(root: Path, keys: dict, *, expired: bool = False):
    candidate=load(next((root/".vibe-control"/"candidates").glob("*.json")))
    contract=load(root/".vibe-control"/"tasks"/"TASK-001.json")
    human_decisions=[{"checkpointId":item["id"],"decision":"PASS"} for item in contract["acceptanceCheckpoints"] if item["type"]=="HUMAN"]
    decision={"schemaVersion":"4.0","decisionId":"DECISION-001","taskId":"TASK-001","candidateId":candidate["candidateId"],"candidateCommit":candidate["commit"],"checkpointSetSha256":candidate["checkpointSetSha256"],"executionPlanSha256":candidate["executionPlanSha256"],"checkpointDecisions":human_decisions,"positioning":candidate["positioning"],"resolvedRuleSet":candidate["resolvedRuleSet"],"scope":candidate["changedPaths"],"owner":{"actorId":"owner"},"decision":"APPROVE","decidedAt":"2026-07-25T02:00:00+08:00","expiresAt":"2020-01-01T00:00:00+00:00" if expired else None}
    if "owner" in keys:
        decision.update({"keyId":"owner-key"}); decision=sign(decision,keys["owner"])
    path=Path(root.parent)/"decision.json"; write(path,decision); return command(root,"accept","--decision",str(path),expect=None)


def install_release_chain(root: Path, keys: dict, *, report_result: str = "PASS", report_findings: list | None = None, use_review_auditor: bool = False):
    """Record a candidate-bound external audit and owner-signed receipt after audit/accept."""
    control = root / ".vibe-control"; runtime_root = control / "runtime" / RUNTIME_VERSION
    candidate_path = next((control/"candidates").glob("*.json")); candidate = load(candidate_path)
    review_path = next((control/"reviews").glob("*.json")); decision_path = next((control/"decisions").glob("*.json"))
    evidence_paths = sorted(path for path in (control/"evidence").glob("*.json") if not path.name.endswith(("attestation.json", "adapter-invocation.json")))
    transcript = control / "external-audits" / "release-audit-transcript.txt"; transcript.parent.mkdir(parents=True, exist_ok=True); transcript.write_text("independent external release audit\n", encoding="utf-8"); commit(root, "add external release audit transcript")
    release_actor = "auditor" if use_review_auditor else "release-auditor"; release_key_id = "auditor-key" if use_review_auditor else "release-auditor-key"; release_key = keys["auditor"] if use_review_auditor else keys["release-auditor"]
    report = {
        "schemaVersion":"4.0", "reportId":"RELEASE-AUDIT-001", "taskId":"TASK-001", "candidateId":candidate["candidateId"], "candidateCommit":candidate["commit"], "candidateTree":candidate["tree"],
        "candidate":ref(root,candidate_path), "keyObjectives":candidate["keyObjectives"], "positioning":candidate["positioning"], "resolvedRuleSet":candidate["resolvedRuleSet"], "review":ref(root,review_path), "evidenceRefs":[ref(root,path) for path in evidence_paths],
        "auditor":{"actorId":release_actor,"sessionId":"release-audit-1"}, "findings":report_findings or [], "transcript":ref(root,transcript), "result":report_result,
        "packageManifestSha256":sha(control/"governance"/"package-manifest.json"), "runtimeManifestSha256":sha(runtime_root/"runtime-manifest.json"), "assuranceMatrixSha256":sha(control/"governance"/"controller-assurance-matrix.json"),
        "auditedAt":"2026-07-25T03:00:00+08:00", "keyId":release_key_id
    }
    report_path = control / "external-audits" / "RELEASE-AUDIT-001.json"; write(report_path, sign(report, release_key)); commit(root, "record signed external release audit")
    receipt = {
        "schemaVersion":"4.0", "version":RUNTIME_VERSION, "taskId":"TASK-001", "candidateId":candidate["candidateId"], "candidateCommit":candidate["commit"], "candidateTree":candidate["tree"],
        "candidate":ref(root,candidate_path), "positioning":candidate["positioning"], "resolvedRuleSet":candidate["resolvedRuleSet"], "decision":ref(root,decision_path), "auditReport":ref(root,report_path),
        "packageManifestSha256":sha(control/"governance"/"package-manifest.json"), "runtimeManifestSha256":sha(runtime_root/"runtime-manifest.json"), "assuranceMatrixSha256":sha(control/"governance"/"controller-assurance-matrix.json"),
        "enableFormalClaims":True, "owner":{"actorId":"owner"}, "keyId":"owner-key", "signedAt":"2026-07-25T04:00:00+08:00"
    }
    receipt_path = runtime_root / "release-receipt.json"; write(receipt_path, sign(receipt, keys["owner"])); commit(root, "record candidate-bound release receipt")
    return report_path, receipt_path


def failing_ids(report: dict) -> set[str]: return {x["id"] for x in report.get("integrity",{}).get("checks",[]) if x["status"] != "PASS"}
