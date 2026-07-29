#!/usr/bin/env python3
"""Baseline capability tests for the Schema 3.2 envelope."""
from __future__ import annotations
import json, sys
from pathlib import Path
import test_v2_support as fx

def test_inspect():
    temp,root,_=fx.setup_project();
    try:
        result=fx.run(sys.executable,str(fx.WRAPPER),"inspect","--project",str(root)); assert json.loads(result.stdout)["status"]=="PASS"
    finally: temp.cleanup()
def test_bootstrap_schema3():
    temp,root,_=fx.setup_project();
    try:
        assert fx.load(root/".vibe-control"/"stage-state.json")["schemaVersion"]=="3.2"
        assert (root/".vibe-control"/"key-objectives-lock.json").is_file()
        assert (root/".vibe-control"/"project-positioning.json").is_file()
        assert (root/".vibe-control"/"resolved-rule-set.json").is_file()
        lock=fx.load(root/".vibe-control"/"project-governance-lock.json")
        assert lock["releaseIntent"]=="PRIVATE_OPERATION" and lock["packageMode"]=="SEALED"
    finally: temp.cleanup()
def test_runtime_bundle_excludes_bytecode_cache():
    temp,root,_=fx.setup_project()
    try:
        runtime=root/".vibe-control"/"runtime"/fx.RUNTIME_VERSION
        assert not any(path.name=="__pycache__" or path.suffix==".pyc" for path in runtime.rglob("*"))
        assert not any("__pycache__" in path or path.endswith(".pyc") for path in fx.git(root,"ls-files").splitlines())
    finally: temp.cleanup()
def test_risk():
    value=json.loads(fx.run(sys.executable,str(fx.WRAPPER),"risk","--score","70").stdout); assert value["data"]["level"]=="R3"
def test_freeze():
    temp,root,_=fx.setup_project();
    try: assert len(list((root/".vibe-control"/"candidates").glob("*.json")))==1
    finally: temp.cleanup()
def test_execute():
    temp,root,_=fx.setup_project();
    try: fx.execute_and_verify(root)
    finally: temp.cleanup()
def test_skip_rejected():
    temp,root,_=fx.setup_project();
    try:
        fx.execute_and_verify(root); path=next(path for path in (root/".vibe-control"/"evidence").glob("*.json") if not path.name.endswith(("attestation.json","adapter-invocation.json"))); value=fx.load(path); value["counters"].update({"passed":0,"skipped":1}); fx.write(path,value); fx.commit(root,"mutate skip"); _,report=fx.command(root,"validate",expect=3); assert "HC-CASE-COUNTERS" in fx.failing_ids(report)
    finally: temp.cleanup()
def test_zero_rejected():
    temp,root,_=fx.setup_project();
    try:
        fx.execute_and_verify(root); path=next(path for path in (root/".vibe-control"/"evidence").glob("*.json") if not path.name.endswith(("attestation.json","adapter-invocation.json"))); value=fx.load(path); value["counters"].update({"executed":0,"passed":0}); fx.write(path,value); fx.commit(root,"mutate zero"); _,report=fx.command(root,"validate",expect=3); assert "HC-CASE-COUNTERS" in fx.failing_ids(report)
    finally: temp.cleanup()
def test_counter_conservation_rejected():
    temp,root,_=fx.setup_project();
    try:
        fx.command(root,"execute","--actor","executor","--session","exec-1"); fx.commit(root,"evidence"); path=next(path for path in (root/".vibe-control"/"evidence").glob("*.json") if not path.name.endswith(("attestation.json","adapter-invocation.json"))); value=fx.load(path); value["counters"]["executed"]=2; fx.write(path,value); fx.commit(root,"mutate conservation"); _,report=fx.command(root,"validate",expect=3); assert "HC-CASE-COUNTERS" in fx.failing_ids(report)
    finally: temp.cleanup()
def test_review():
    temp,root,keys=fx.setup_project(include_keys=False);
    try: fx.execute_and_verify(root); result,_=fx.advance_audit(root,keys); assert result.returncode==0
    finally: temp.cleanup()
def test_accept():
    temp,root,keys=fx.setup_project(include_keys=False);
    try: fx.execute_and_verify(root); fx.advance_audit(root,keys); fx.commit(root,"audit"); result,_=fx.advance_accept(root,keys); assert result.returncode==0
    finally: temp.cleanup()
def test_release():
    temp,root,keys=fx.setup_project(include_keys=False);
    try:
        fx.execute_and_verify(root); fx.advance_audit(root,keys); fx.commit(root,"audit"); fx.advance_accept(root,keys); fx.commit(root,"accept")
        expected = 0 if fx.package_formal_enabled() else 2
        _,report=fx.command(root,"release-check",expect=expected)
        if fx.package_formal_enabled():
            assert report["formal"]["eligible"] is True and report["formal"]["maxClaimLevel"]=="ACCEPTED" and report["data"]["privateOperationReady"] is True
        else:
            assert report["formal"]["eligible"] is False and "HC-ASSURANCE-MATRIX-FORMAL" in report["formal"]["blockers"]
        assert "HC-RELEASE-RECEIPT" not in report["formal"]["blockers"]
    finally: temp.cleanup()
def test_schema32_does_not_remigrate():
    temp,root,_=fx.setup_project();
    try:
        _,report=fx.command(root,"migrate","--plan",expect=2)
        assert report["error"]["id"]=="HC-MIGRATION-SOURCE-VERSION" and report["formal"]["eligible"] is False
    finally: temp.cleanup()
TESTS=[test_inspect,test_bootstrap_schema3,test_runtime_bundle_excludes_bytecode_cache,test_risk,test_freeze,test_execute,test_skip_rejected,test_zero_rejected,test_counter_conservation_rejected,test_review,test_accept,test_release,test_schema32_does_not_remigrate]
def main():
    out=[]
    for test in TESTS:
        try:test();out.append({"test":test.__name__,"status":"PASS"})
        except Exception as exc:out.append({"test":test.__name__,"status":"FAIL","error":str(exc)})
    ok=all(x["status"]=="PASS" for x in out);print(json.dumps({"status":"PASS" if ok else "FAIL","tests":out},ensure_ascii=False,indent=2));return 0 if ok else 1
if __name__=="__main__":raise SystemExit(main())
