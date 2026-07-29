#!/usr/bin/env python3
"""Inherited composition, signature, dependency, and reinstall tests for 0.3.6."""
from __future__ import annotations
import json
import sys
from datetime import datetime
import test_v2_support as fx

def external_attestation(root, key=None, *, signer=None, observation="blackbox-observed"):
    candidate=fx.load(next((root/".vibe-control"/"candidates").glob("*.json"))); catalog=fx.load(root/".vibe-control"/"case-catalog.json"); case=catalog["cases"][0]
    evidence_dir=root/".vibe-control"/"evidence"; evidence_dir.mkdir(parents=True,exist_ok=True)
    transcript=evidence_dir/"external-transcript.txt"; transcript.write_text("external PASS\n",encoding="utf-8")
    invocation=evidence_dir/"external-adapter-invocation.json"; fx.write(invocation,{"adapter":case["adapter"],"operation":"fixture-blackbox","toolVersion":"external-fixture-1.0","status":"PASS"})
    fx.commit(root,"external raw evidence")
    evidence={"schemaVersion":"3.2","evidenceId":"EXT-001","taskId":"TASK-001","candidateId":candidate["candidateId"],"candidateCommit":candidate["commit"],"checkpointSetSha256":candidate["checkpointSetSha256"],"checkpointIds":["CP-001"],"positioning":candidate["positioning"],"resolvedRuleSet":candidate["resolvedRuleSet"],"caseId":"CASE-001","caseHash":fx.hashlib.sha256(fx.canonical(case)).hexdigest(),"oracleHash":fx.hashlib.sha256(fx.canonical(case["oracle"])).hexdigest(),"inputHash":fx.hashlib.sha256(fx.canonical(candidate["inputBindings"])).hexdigest(),"executor":{"actorId":"executor","sessionId":"external-1"},"observation":observation,"adapter":case["adapter"],"capabilitiesObserved":case["capabilities"],"adapterInvocation":fx.ref(root,invocation),"externalTranscript":fx.ref(root,transcript),"toolVersion":"external-fixture-1.0","operation":"fixture-blackbox","command":["external-adapter"],"startedAt":"2026-07-25T00:00:00+08:00","finishedAt":"2026-07-25T00:00:01+08:00","exitCode":0,"counters":{"executed":1,"passed":1,"failed":0,"skipped":0},"transcript":fx.ref(root,transcript),"artifacts":[],"result":"PASS"}
    att={"schemaVersion":"3.2","evidence":evidence}
    if key or signer:
        att["keyId"]="executor-key"; return fx.sign(att, signer or key)
    return att

def setup_r3_release_project():
    temp,root,keys=fx.setup_project(risk="R3",observation="blackbox-observed",release_intent="EXTERNAL_RELEASE")
    att=external_attestation(root,keys["executor"]); path=root.parent/"att.json"; fx.write(path,att)
    fx.command(root,"ingest","--attestation",str(path)); fx.commit(root,"ingest")
    fx.command(root,"validate",expect=2); fx.commit(root,"derive verified state")
    fx.command(root,"validate",expect=2)
    return temp,root,keys

def test_r3_signed_path():
    temp,root,keys=setup_r3_release_project()
    try:
        _,report=fx.command(root,"validate",expect=2); assert report["formal"]["eligible"] is False and "HC-RISK-REVIEW-GATE" in report["formal"]["blockers"]
        result,_=fx.advance_audit(root,keys); assert result.returncode==0; fx.commit(root,"audit"); result,_=fx.advance_accept(root,keys); assert result.returncode==0; fx.commit(root,"accept")
        fx.install_release_chain(root,keys)
        if fx.package_formal_enabled():
            _,transition=fx.command(root,"release-check",expect=2); assert transition["state"]["declared"]["phase"]=="RELEASE_READY"; fx.commit(root,"advance release state")
            _,report=fx.command(root,"release-check",expect=0); assert report["formal"]["eligible"] is True and report["formal"]["maxClaimLevel"]=="RELEASE_READY"
        else:
            _,report=fx.command(root,"release-check",expect=2); assert report["formal"]["eligible"] is False and "HC-ASSURANCE-MATRIX-FORMAL" in report["formal"]["blockers"]
    finally: temp.cleanup()
def test_wrong_executor_key():
    temp,root,keys=fx.setup_project(risk="R3",observation="blackbox-observed",release_intent="EXTERNAL_RELEASE")
    try:
        att=external_attestation(root,keys["executor"],signer=keys["owner"]); path=root.parent/"att.json"; fx.write(path,att); result,report=fx.command(root,"ingest","--attestation",str(path),expect=3); assert report["error"]["id"]=="HC-EXECUTOR-SIGNATURE"
    finally: temp.cleanup()
def test_r3_controller_local_execution_is_eligible():
    temp,root,_=fx.setup_project(risk="R3",release_intent="EXTERNAL_RELEASE")
    try:
        fx.command(root,"execute","--actor","executor","--session","local"); fx.commit(root,"local evidence"); _,report=fx.command(root,"validate",expect=2)
        assert report["state"]["derived"]["phase"]=="VERIFIED"
        assert "HC-CASE-OBSERVATION-ELIGIBILITY" not in fx.failing_ids(report)
        assert "HC-EXECUTOR-SIGNATURE" not in fx.failing_ids(report)
        assert "HC-RISK-REVIEW-GATE" in report["formal"]["blockers"]
    finally: temp.cleanup()
def test_private_blackbox_attestation_does_not_require_key():
    temp,root,_=fx.setup_project(observation="blackbox-observed",include_keys=False)
    try:
        path=root.parent/"att.json"; fx.write(path,external_attestation(root))
        result,_=fx.command(root,"ingest","--attestation",str(path)); assert result.returncode==0; fx.commit(root,"private blackbox attestation")
        _,report=fx.command(root,"validate",expect=2); assert report["state"]["derived"]["phase"]=="VERIFIED" and "HC-EXECUTOR-SIGNATURE" not in fx.failing_ids(report)
    finally: temp.cleanup()
def test_wrong_auditor_signature():
    temp,root,keys=setup_r3_release_project()
    try:
        bad=dict(keys); bad["auditor"]=keys["owner"]; result,report=fx.advance_audit(root,bad); assert result.returncode==3 and report["error"]["id"]=="HC-AUDITOR-SIGNATURE"
    finally: temp.cleanup()

def test_r3_unsigned_review_and_decision_rejected():
    temp,root,keys=setup_r3_release_project()
    try:
        result,report=fx.advance_audit(root,{},review_id="REVIEW-UNSIGNED"); assert result.returncode==3 and report["error"]["id"]=="HC-AUDITOR-SIGNATURE"
        result,_=fx.advance_audit(root,keys); assert result.returncode==0; fx.commit(root,"audit")
        result,report=fx.advance_accept(root,{}); assert result.returncode==3 and report["error"]["id"]=="HC-OWNER-SIGNATURE"
    finally: temp.cleanup()
def test_expired_approval():
    temp,root,keys=fx.setup_project()
    try:
        fx.execute_and_verify(root); fx.advance_audit(root,keys); fx.commit(root,"audit"); result,report=fx.advance_accept(root,keys,expired=True); assert result.returncode==4 and report["error"]["id"]=="HC-DECISION-EXPIRED"
    finally: temp.cleanup()
def test_dependency_mismatch_blocks():
    temp,root,_=fx.setup_project()
    try:
        path=root/".vibe-control"/"runtime"/fx.RUNTIME_VERSION/"dependency-lock.json"; value=fx.load(path); value["packages"]["jsonschema"]="0.0.0"; fx.write(path,value); fx.commit(root,"dependency attack"); _,report=fx.command(root,"validate",expect=2); assert "HC-DEPENDENCY-JSONSCHEMA" in fx.failing_ids(report)
    finally: temp.cleanup()

def test_missing_dependencies_emit_stable_json():
    temp,root,_=fx.setup_project()
    try:
        result=fx.run(sys.executable,"-S",str(fx.WRAPPER),"inspect","--project",str(root),expect=2)
        assert result.returncode==2 and "Traceback" not in result.stderr and "Traceback" not in result.stdout
        report=json.loads(result.stdout); assert report["status"]=="BLOCKED" and report["error"]["id"]=="DEPENDENCY_BLOCKED"
        assert report["formal"]["eligible"] is False and any(item["id"].startswith("HC-DEPENDENCY-") and item["status"]=="BLOCKED" for item in report["integrity"]["checks"])
    finally: temp.cleanup()

def test_external_contract_path_returns_stable_path_safety():
    temp,root,_=fx.setup_project()
    try:
        outside=root.parent/"outside-contract.json"; outside.write_bytes((root/".vibe-control"/"tasks"/"TASK-001.json").read_bytes())
        result,report=fx.command(root,"lock-task","--contract",str(outside),expect=3)
        assert report["error"]["id"]=="HC-PATH-SAFETY" and "CLI-INTERNAL-ERROR" not in fx.failing_ids(report)
        assert report["formal"]["eligible"] is False and "Traceback" not in result.stdout and "Traceback" not in result.stderr
    finally: temp.cleanup()
def test_receipt_binding_drift():
    temp,root,keys=setup_r3_release_project()
    try:
        fx.advance_audit(root,keys); fx.commit(root,"audit"); fx.advance_accept(root,keys); fx.commit(root,"accept"); fx.install_release_chain(root,keys)
        path=root/".vibe-control"/"governance"/"controller-assurance-matrix.json"; value=fx.load(path); value["matrixId"]="drift"; fx.write(path,value); fx.commit(root,"matrix drift"); _,report=fx.command(root,"validate",expect=None); assert report["status"] in {"FAIL","INVALIDATED"} and "HC-RELEASE-RECEIPT" in fx.failing_ids(report)
    finally: temp.cleanup()
def test_receipt_cannot_replay_for_new_candidate():
    temp,root,keys=setup_r3_release_project()
    try:
        fx.advance_audit(root,keys); fx.commit(root,"audit"); fx.advance_accept(root,keys); fx.commit(root,"accept"); fx.install_release_chain(root,keys)
        candidate_path=next((root/".vibe-control"/"candidates").glob("*.json")); candidate=fx.load(candidate_path); candidate["candidateId"]="candidate-TASK-001-aaaaaaaaaaaa"; candidate["commit"]="a"*40; candidate["tree"]="b"*40; fx.write(candidate_path,candidate); fx.commit(root,"candidate identity replay attack")
        _,report=fx.command(root,"validate",expect=None); assert report["formal"]["eligible"] is False and "HC-RELEASE-RECEIPT-CANDIDATE" in fx.failing_ids(report)
    finally: temp.cleanup()
def test_cross_role_key_reuse_rejected_at_bootstrap():
    temp=fx.tempfile.TemporaryDirectory(prefix="vibe-control-key-separation-"); base=fx.Path(temp.name); root=base/"project"; root.mkdir()
    try:
        fx.git(root,"init"); fx.git(root,"config","user.email","fixture@example.invalid"); fx.git(root,"config","user.name","Fixture")
        (root/"PROJECT_BRIEF.md").write_text("# Fixture\n",encoding="utf-8")
        fx.write_objective_files(root)
        positioning=fx.positioning_axes("EXTERNAL_RELEASE")
        summary_hash=fx.hashlib.sha256(fx.json.dumps(positioning,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        fx.write(root/"POSITIONING_CONFIRMATION.json",{"actorId":"owner","summary":"fixture positioning","summarySha256":summary_hash})
        fx.write(root/"AUTOMATION_CONFIRMATION.json",{"actorId":"owner","decision":"CONFIRM"})
        fx.commit(root,"initial")
        shared=fx.Ed25519PrivateKey.generate(); owner=fx.Ed25519PrivateKey.generate()
        trusted=[{"keyId":"executor-key","actorId":"executor","role":"executor","publicKey":fx.public_b64(shared)},{"keyId":"auditor-key","actorId":"auditor","role":"auditor","publicKey":fx.public_b64(shared)},{"keyId":"release-auditor-key","actorId":"release-auditor","role":"release-auditor","publicKey":fx.public_b64(fx.Ed25519PrivateKey.generate())},{"keyId":"owner-key","actorId":"owner","role":"owner","publicKey":fx.public_b64(owner)}]
        spec=fx.valid_bootstrap_spec(project_id="reuse",release_intent="EXTERNAL_RELEASE",trusted_keys=trusted)
        spec_path=base/"bootstrap.json"; fx.write(spec_path,spec); result,report=fx.run(fx.sys.executable,str(fx.WRAPPER),"bootstrap","--project",str(root),"--spec",str(spec_path),expect=None),None; report=fx.json.loads(result.stdout); assert result.returncode==3 and report["error"]["id"]=="HC-ROLE-KEY-SEPARATION"
    finally: temp.cleanup()
def test_ignored_runner_is_not_executed_from_project_worktree():
    temp,root,_=fx.setup_project(command_script="ignored_runner.py",ignored_runner_case=True)
    try:
        (root/"ignored_runner.py").write_text("print('OK')\n",encoding="utf-8"); assert not fx.git(root,"status","--porcelain"); fx.command(root,"execute","--actor","executor","--session","exec-1",expect=3); fx.commit(root,"record immutable worktree execution"); _,report=fx.command(root,"validate",expect=3); assert report["formal"]["eligible"] is False and ({"HC-CASE-COUNTERS","HC-REQUIRED-CASE-COVERAGE"}&fx.failing_ids(report))
    finally: temp.cleanup()
def test_release_auditor_cannot_reuse_review_auditor_role():
    temp,root,keys=setup_r3_release_project()
    try:
        fx.advance_audit(root,keys); fx.commit(root,"audit"); fx.advance_accept(root,keys); fx.commit(root,"accept"); fx.install_release_chain(root,keys,use_review_auditor=True); _,report=fx.command(root,"validate",expect=None); assert report["formal"]["eligible"] is False and "HC-RELEASE-AUDITOR-KEY" in fx.failing_ids(report)
    finally: temp.cleanup()
def test_schema32_is_not_migrated_again():
    temp,root,_=fx.setup_project()
    try:
        before=fx.git(root,"status","--porcelain")
        result,report=fx.command(root,"migrate","--plan",expect=2)
        assert report["error"]["id"]=="HC-MIGRATION-SOURCE-VERSION"
        assert not (root/".vibe-control"/"legacy").exists() and fx.git(root,"status","--porcelain")==before
    finally: temp.cleanup()
def test_handoff_binds_current_objects():
    temp,root,_=fx.setup_project()
    try:
        _,report=fx.command(root,"handoff",expect=None); path=report["data"]["handoff"]; value=fx.load(fx.Path(path)); assert value["taskId"]=="TASK-001" and value["candidateId"]
    finally: temp.cleanup()
def test_authority_hash_drift():
    temp,root,_=fx.setup_project()
    try:
        fx.execute_and_verify(root)
        (root/"PROJECT_BRIEF.md").write_text("drift\n",encoding="utf-8"); fx.commit(root,"authority drift"); _,report=fx.command(root,"validate",expect=4); assert "HC-OBJECTIVES-SOURCE-1" in fx.failing_ids(report)
    finally: temp.cleanup()
def test_product_change_invalidates_candidate():
    temp,root,_=fx.setup_project()
    try:
        fx.execute_and_verify(root)
        (root/"fixture.py").write_text("print('changed')\n",encoding="utf-8"); fx.commit(root,"product drift"); _,report=fx.command(root,"validate",expect=4); assert "HC-CANDIDATE-HEAD" in fx.failing_ids(report)
    finally: temp.cleanup()

TESTS=[test_r3_signed_path,test_wrong_executor_key,test_r3_controller_local_execution_is_eligible,test_private_blackbox_attestation_does_not_require_key,test_wrong_auditor_signature,test_r3_unsigned_review_and_decision_rejected,test_expired_approval,test_dependency_mismatch_blocks,test_missing_dependencies_emit_stable_json,test_external_contract_path_returns_stable_path_safety,test_receipt_binding_drift,test_receipt_cannot_replay_for_new_candidate,test_cross_role_key_reuse_rejected_at_bootstrap,test_ignored_runner_is_not_executed_from_project_worktree,test_release_auditor_cannot_reuse_review_auditor_role,test_schema32_is_not_migrated_again,test_handoff_binds_current_objects,test_authority_hash_drift,test_product_change_invalidates_candidate]
def main():
    out=[]
    for test in TESTS:
        try:test();out.append({"test":test.__name__,"status":"PASS"})
        except Exception as exc:out.append({"test":test.__name__,"status":"FAIL","error":str(exc)})
    ok=all(x["status"]=="PASS" for x in out);print(json.dumps({"status":"PASS" if ok else "FAIL","tests":out},ensure_ascii=False,indent=2));return 0 if ok else 1
if __name__=="__main__":raise SystemExit(main())
