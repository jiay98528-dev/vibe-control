#!/usr/bin/env python3
"""External-audit-derived mutations. Each must hit its own stable check ID."""
from __future__ import annotations
import argparse, json, os, subprocess, sys, tempfile, time
from pathlib import Path
import test_v2_support as fx
import bounded_test_runner as bounded

DEFAULT_CASE_TIMEOUT_SECONDS = int(os.environ.get("VIBE_CONTROL_ASSURANCE_CASE_TIMEOUT", "180"))
DEFAULT_SUITE_TIMEOUT_SECONDS = int(os.environ.get("VIBE_CONTROL_ASSURANCE_SUITE_TIMEOUT", "240"))

def reject(root, ids):
    _,report=fx.command(root,"validate",expect=None); assert report["status"] in {"FAIL","BLOCKED","INVALIDATED"}; assert report["formal"]["eligible"] is False; actual=fx.failing_ids(report); assert actual & ids, f"expected {ids}, got {actual}"

def test_release_ready_without_review_or_decision_is_blocked():
    temp,root,_=fx.setup_project(risk="R3")
    try:
        path=root/".vibe-control"/"stage-state.json"; state=fx.load(path); state.update({"phase":"RELEASE_READY","claimLevel":"RELEASE_READY","health":"CLEAR"}); state["phaseHistory"].extend([{"from":"CANDIDATE_FROZEN","to":"VERIFIED","at":"2026-07-25T01:00:00+08:00","reason":"attack"},{"from":"VERIFIED","to":"AUDITED","at":"2026-07-25T01:01:00+08:00","reason":"attack"},{"from":"AUDITED","to":"ACCEPTED","at":"2026-07-25T01:02:00+08:00","reason":"attack"},{"from":"ACCEPTED","to":"RELEASE_READY","at":"2026-07-25T01:03:00+08:00","reason":"attack"}]); fx.write(path,state); fx.commit(root,"forge release state"); reject(root,{"HC-REVIEW-REQUIRED","HC-DECISION-REQUIRED","HC-RELEASE-PREREQUISITES"})
    finally: temp.cleanup()
def test_task_claim_ceiling_is_enforced():
    temp,root,_=fx.setup_project(task_ceiling="DEVELOPMENT_CHECKED")
    try:
        path=root/".vibe-control"/"stage-state.json"; state=fx.load(path); state["claimLevel"]="VERIFIED"; fx.write(path,state); fx.commit(root,"exceed ceiling"); reject(root,{"HC-CLAIM-TASK-CEILING"})
    finally: temp.cleanup()
def test_bootstrap_requires_explicit_release_intent():
    spec=fx.valid_bootstrap_spec(project_id="missing-intent")
    spec.pop("releaseIntent")
    temp,root,result=fx.bootstrap_raw(spec)
    try:
        report=json.loads(result.stdout); assert result.returncode==3 and report["error"]["id"]=="HC-POSITIONING-SCHEMA"
    finally: temp.cleanup()
def test_release_intent_paths_are_enforced():
    base_spec=fx.valid_bootstrap_spec(project_id="intent",release_intent="EXTERNAL_RELEASE")
    temp,root,result=fx.bootstrap_raw(base_spec)
    try:
        assert result.returncode==2; fx.commit(root,"bootstrap")
        contract=fx.task_contract(risk="R2",task_ceiling="RELEASE_READY")
        contract["goal"]="release"
        path=root/".vibe-control"/"tasks"/"TASK-001.json"; fx.write(path,contract); fx.commit(root,"add r2 release task")
        result,report=fx.command(root,"lock-task","--contract",str(path),expect=3); assert report["error"]["id"]=="HC-EXTERNAL-RELEASE-R3"
        contract["risk"]="R3"; fx.write(path,contract); fx.commit(root,"make task r3")
        result,report=fx.command(root,"lock-task","--contract",str(path),expect=3); assert report["error"]["id"]=="HC-R3-TRUSTED-KEYS"
    finally: temp.cleanup()
def test_local_experiment_caps_and_blocks_release():
    temp,root,keys=fx.setup_project(release_intent="LOCAL_EXPERIMENT")
    try:
        fx.execute_and_verify(root); result,report=fx.command(root,"release-check",expect=2); assert "HC-RELEASE-INTENT-LOCAL" in report["formal"]["blockers"]
        result,_=fx.advance_audit(root,keys); assert result.returncode==0; fx.commit(root,"local audit")
        result,report=fx.advance_accept(root,keys); assert result.returncode==2 and report["error"]["id"]=="HC-RELEASE-INTENT-CEILING"
    finally: temp.cleanup()
def test_private_r3_does_not_require_keys_or_receipt():
    temp,root,keys=fx.setup_project(risk="R3",include_keys=False)
    try:
        fx.execute_and_verify(root); result,_=fx.advance_audit(root,keys); assert result.returncode==0; fx.commit(root,"private r3 audit")
        result,_=fx.advance_accept(root,keys); assert result.returncode==0; fx.commit(root,"private r3 accept")
        expected=0 if fx.package_formal_enabled() else 2; _,report=fx.command(root,"release-check",expect=expected); failing=fx.failing_ids(report); assert "HC-R3-TRUSTED-KEYS" not in failing and "HC-RELEASE-RECEIPT" not in failing
        if fx.package_formal_enabled(): assert report["formal"]["eligible"] is True and report["formal"]["maxClaimLevel"]=="ACCEPTED"
    finally: temp.cleanup()
def test_schema_is_executed():
    temp,root,_=fx.setup_project()
    try:
        fx.command(root,"execute","--actor","executor","--session","exec-1"); fx.commit(root,"evidence"); path=fx.main_evidence_path(root); value=fx.load(path); value.pop("command"); value["unexpected"]=True; fx.write(path,value); fx.commit(root,"break schema"); reject(root,{"HC-SCHEMA-EVIDENCE"})
    finally: temp.cleanup()
def test_bad_top_level_returns_stable_json():
    temp,root,_=fx.setup_project()
    try:
        path=root/".vibe-control"/"stage-state.json"; path.write_text("[]\n",encoding="utf-8"); fx.commit(root,"bad state"); result,report=fx.command(root,"validate",expect=None); assert result.returncode==3; assert report["error"]["id"] if "error" in report else "HC-SCHEMA-STATE" in fx.failing_ids(report); assert "HC-SCHEMA-STATE" in fx.failing_ids(report)
    finally: temp.cleanup()
def test_candidate_task_identity_closes():
    temp,root,_=fx.setup_project()
    try:
        path=next((root/".vibe-control"/"candidates").glob("*.json")); value=fx.load(path); value["taskId"]="OTHER"; fx.write(path,value); fx.commit(root,"cross task"); reject(root,{"HC-CANDIDATE-TASK-IDENTITY","HC-CANDIDATE-CONTRACT-IDENTITY"})
    finally: temp.cleanup()
def test_freeze_rejects_forbidden_diff():
    temp,root,_=fx.setup_project()
    try:
        before=len(list((root/".vibe-control"/"candidates").glob("*.json"))); (root/"forbidden.txt").write_text("secret\n",encoding="utf-8"); fx.commit(root,"forbidden change"); result,report=fx.command(root,"freeze","--actor","implementer","--session","impl-1",expect=None); assert result.returncode==3; assert report["error"]["id"]=="HC-FREEZE-PATH-ENVELOPE"; assert len(list((root/".vibe-control"/"candidates").glob("*.json")))==before
    finally: temp.cleanup()
def test_declared_observation_cannot_cover_required_case():
    temp,root,_=fx.setup_project()
    try:
        fx.command(root,"execute","--actor","executor","--session","exec-1"); fx.commit(root,"evidence"); path=fx.main_evidence_path(root); value=fx.load(path); value["observation"]="declared"; fx.write(path,value); fx.commit(root,"declared attack"); reject(root,{"HC-CASE-OBSERVATION-ELIGIBILITY","HC-CASE-PROVENANCE"})
    finally: temp.cleanup()
def test_failed_health_is_not_eligible():
    temp,root,_=fx.setup_project()
    try:
        path=root/".vibe-control"/"stage-state.json"; value=fx.load(path); value["health"]="FAILED"; fx.write(path,value); fx.commit(root,"failed health"); reject(root,{"HC-HEALTH-CLAIM-COMPATIBILITY"})
    finally: temp.cleanup()
def test_phase_history_closes():
    temp,root,_=fx.setup_project()
    try:
        path=root/".vibe-control"/"stage-state.json"; value=fx.load(path); value["phaseHistory"]=value["phaseHistory"][:-1]; fx.write(path,value); fx.commit(root,"break history"); reject(root,{"HC-PHASE-HISTORY-TAIL","HC-PHASE-HISTORY-CONTINUITY"})
    finally: temp.cleanup()
def test_cli_error_surface_is_stable():
    result=fx.run(sys.executable,str(fx.WRAPPER),"unknown",expect=None); report=json.loads(result.stdout); assert result.returncode==3 and report["error"]["id"]=="CLI-INVALID-ARGUMENTS" and not result.stderr
def test_candidate_manifest_diff_recomputed():
    temp,root,_=fx.setup_project()
    try:
        path=next((root/".vibe-control"/"candidates").glob("*.json")); candidate=fx.load(path); (root/"forbidden.txt").write_text("hidden\n",encoding="utf-8"); bad_commit=fx.commit(root,"hidden forbidden product"); candidate.update({"commit":bad_commit,"tree":fx.git(root,"show","-s","--format=%T",bad_commit),"changedPaths":["fixture.py"]}); fx.write(path,candidate); fx.commit(root,"forge candidate manifest"); reject(root,{"HC-CANDIDATE-DIFF","HC-CANDIDATE-PATH-ENVELOPE"})
    finally: temp.cleanup()
def test_r2_attestations_do_not_require_keys():
    temp,root,keys=fx.setup_project(include_keys=False)
    try:
        fx.execute_and_verify(root); result,_=fx.advance_audit(root,keys); assert result.returncode==0; fx.commit(root,"r2 audit"); result,_=fx.advance_accept(root,keys); assert result.returncode==0; fx.commit(root,"r2 accept"); expected=0 if fx.package_formal_enabled() else 2; _,report=fx.command(root,"release-check",expect=expected); assert "HC-RELEASE-RECEIPT" not in report["formal"]["blockers"]
        if fx.package_formal_enabled(): assert report["formal"]["eligible"] is True and report["formal"]["maxClaimLevel"]=="ACCEPTED"
        else: assert "HC-ASSURANCE-MATRIX-FORMAL" in report["formal"]["blockers"]
    finally: temp.cleanup()

def test_signed_failed_review_cannot_advance_or_qualify():
    temp,root,keys=fx.setup_project()
    try:
        fx.execute_and_verify(root); result,report=fx.advance_audit(root,keys,result="FAIL"); assert result.returncode==3 and report["error"]["id"]=="HC-CHECKPOINT-REVIEW-TOTAL"
    finally: temp.cleanup()

def test_candidate_input_bindings_cannot_be_substituted():
    temp,root,_=fx.setup_project()
    try:
        path=next((root/".vibe-control"/"candidates").glob("*.json")); value=fx.load(path); value["inputBindings"]=[]; fx.write(path,value); fx.commit(root,"substitute candidate inputs"); reject(root,{"HC-CANDIDATE-INPUT-CLOSURE"})
    finally: temp.cleanup()

def test_managed_identifier_cannot_escape_control_directory():
    temp,root,_=fx.setup_project()
    try:
        path=next((root/".vibe-control"/"candidates").glob("*.json")); value=fx.load(path); value["candidateId"]="../../outside"; fx.write(path,value); fx.commit(root,"unsafe candidate identifier"); reject(root,{"HC-IDENTIFIER-SAFETY"})
    finally: temp.cleanup()

TESTS=[test_release_ready_without_review_or_decision_is_blocked,test_task_claim_ceiling_is_enforced,test_bootstrap_requires_explicit_release_intent,test_release_intent_paths_are_enforced,test_local_experiment_caps_and_blocks_release,test_private_r3_does_not_require_keys_or_receipt,test_schema_is_executed,test_bad_top_level_returns_stable_json,test_candidate_task_identity_closes,test_freeze_rejects_forbidden_diff,test_declared_observation_cannot_cover_required_case,test_failed_health_is_not_eligible,test_phase_history_closes,test_cli_error_surface_is_stable,test_candidate_manifest_diff_recomputed,test_r2_attestations_do_not_require_keys,test_signed_failed_review_cannot_advance_or_qualify,test_candidate_input_bindings_cannot_be_substituted,test_managed_identifier_cannot_escape_control_directory]


def run_worker(test_name: str) -> int:
    tests = {test.__name__: test for test in TESTS}
    test = tests.get(test_name)
    if test is None:
        print(json.dumps({"test": test_name, "status": "FAIL", "checkId": "ASSURANCE-UNKNOWN-CASE"}, ensure_ascii=False))
        return 1
    started = time.monotonic()
    try:
        test()
        result = {"test": test_name, "status": "PASS"}
        exit_code = 0
    except Exception as exc:
        result = {
            "test": test_name,
            "status": "FAIL",
            "checkId": "ASSURANCE-CASE-FAIL",
            "errorType": type(exc).__name__,
            "error": str(exc),
        }
        exit_code = 1
    result["durationSeconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return exit_code


def run_supervised_command(
    test_name: str,
    temp_root: Path,
    command: list[str],
    timeout_seconds: int,
) -> dict:
    return bounded.run_supervised_command(
        test_name, temp_root, command, timeout_seconds,
        identity_field="test",
        protocol_id="ASSURANCE-CASE-PROTOCOL",
        timeout_id="ASSURANCE-CASE-TIMEOUT",
    )


def run_supervised(test_name: str, temp_root: Path, timeout_seconds: int = DEFAULT_CASE_TIMEOUT_SECONDS) -> dict:
    return run_supervised_command(
        test_name,
        temp_root,
        [sys.executable, str(Path(__file__).resolve()), "--case", test_name],
        timeout_seconds,
    )


def build_report(out: list[dict], duration_seconds: float) -> dict:
    result_counters = bounded.counters(out)
    ok = bool(out) and result_counters["passed"] == len(out)
    effective_formal = ok and fx.package_formal_enabled()
    return {
        "status": "PASS" if ok else "FAIL",
        "readiness": "FORMAL_GATE_READY" if effective_formal else ("AWAITING_EXTERNAL_VALIDATION" if ok else "DIAGNOSTIC"),
        "formalClaimsAllowed": effective_formal,
        "durationSeconds": round(duration_seconds, 3),
        "counters": result_counters,
        "tests": out,
    }


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(add_help=True)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--case")
    parser.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--case-timeout", type=int, default=DEFAULT_CASE_TIMEOUT_SECONDS)
    parser.add_argument("--suite-timeout", type=int, default=DEFAULT_SUITE_TIMEOUT_SECONDS)
    try:
        args = parser.parse_args(argv)
        names = [test.__name__ for test in TESTS]
        if args.list and args.case:
            raise ValueError("--list and --case are mutually exclusive")
        if args.jobs < 1 or args.case_timeout < 1 or args.suite_timeout < 1:
            raise ValueError("jobs and timeouts must be positive integers")
        if args.list:
            print(json.dumps({"status": "PASS", "cases": names}, ensure_ascii=False))
            return 0
        if args.case:
            return run_worker(args.case)
    except ValueError as exc:
        print(json.dumps({"status": "FAIL", "checkId": "ASSURANCE-INVALID-ARGUMENTS", "error": str(exc)}, ensure_ascii=False))
        return 1
    with tempfile.TemporaryDirectory(prefix="vibe-control-assurance-suite-", ignore_cleanup_errors=True) as temp:
        out, duration = bounded.run_suite(
            names,
            command_for=lambda name: [sys.executable, str(Path(__file__).resolve()), "--case", name],
            temp_root=Path(temp),
            jobs=args.jobs,
            case_timeout=args.case_timeout,
            suite_timeout=args.suite_timeout,
            identity_field="test",
            protocol_id="ASSURANCE-CASE-PROTOCOL",
            timeout_id="ASSURANCE-CASE-TIMEOUT",
            suite_timeout_id="ASSURANCE-SUITE-TIMEOUT",
        )
    report = build_report(out, duration)
    report.update({"jobs": args.jobs, "caseTimeoutSeconds": args.case_timeout, "suiteTimeoutSeconds": args.suite_timeout})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
