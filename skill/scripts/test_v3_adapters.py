#!/usr/bin/env python3
"""Proof-boundary regressions for the 0.3.5 runtime adapters."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "assets" / "project-control" / "runtime"
sys.path.insert(0, str(RUNTIME))

from vibe_runtime.project_rules import compile_positioning  # noqa: E402
from vibe_runtime.positioning_control import coverage_check  # noqa: E402


def _compile(spec: dict, project: Path) -> dict:
    return compile_positioning(spec, project, RUNTIME)


def _adapters(result: dict) -> dict[str, dict]:
    return {item.get("adapterId", item.get("id")): item for item in result["canonical"]["runtimeAdapters"]}


def test_versioned_adapter_descriptors_have_explicit_proof_limits() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-adapters-") as temp:
        result = _compile({"runtimeAdapters": ["generic-command", "browser-runtime", "godot-runtime"]}, Path(temp))
    adapters = _adapters(result)
    assert set(adapters) == {"generic-command", "browser-runtime", "godot-runtime"}, sorted(adapters)
    descriptor_schema = json.loads((RUNTIME / "schemas" / "adapter-descriptor.schema.json").read_text(encoding="utf-8-sig"))
    for descriptor in adapters.values():
        errors = list(Draft202012Validator(descriptor_schema).iter_errors(descriptor))
        assert not errors, f"adapter descriptor/schema drift: {[item.message for item in errors]}"
        assert descriptor["version"]
        assert descriptor["evidenceCapabilities"]
        assert descriptor["doesNotProve"]
        assert descriptor["environmentLimits"]
        assert descriptor["degradationLimits"]


def test_browser_evidence_cannot_prove_native_shell_or_target_hardware() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-browser-") as temp:
        project = Path(temp)
        (project / "playwright.config.ts").write_text("export default {};\n", encoding="utf-8")
        (project / "src-tauri").mkdir()
        (project / "src-tauri" / "tauri.conf.json").write_text("{}\n", encoding="utf-8")
        result = _compile({"runtimeAdapters": ["browser-runtime"]}, project)
    browser = _adapters(result)["browser-runtime"]
    limits = " ".join(browser["doesNotProve"] + browser["degradationLimits"]).lower()
    assert "native shell" in limits and "target hardware" in limits
    assert any(item["signal"] == "Browser" and item["adapter"] == "browser-runtime" for item in result["canonical"]["projectSignals"]), result["canonical"]["projectSignals"]
    assert "PROJECT-SIGNAL-TAURI" in {item["id"] for item in result["investigations"]}


def test_godot_descriptor_binds_runtime_and_limits_headless_proof() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-godot-") as temp:
        project = Path(temp)
        (project / "project.godot").write_text("[application]\nconfig/name=\"Fixture\"\n", encoding="utf-8")
        result = _compile({"runtimeAdapters": ["godot-runtime"]}, project)
    godot = _adapters(result)["godot-runtime"]
    proof = " ".join(godot["evidenceCapabilities"]).lower()
    limits = " ".join(godot["doesNotProve"] + godot["degradationLimits"]).lower()
    assert "project.godot" in proof and "version" in proof
    assert "headless" in limits and "game feel" in limits
    assert any(item["signal"] == "Godot" and item["adapter"] == "godot-runtime" for item in result["canonical"]["projectSignals"]), result["canonical"]["projectSignals"]


def test_tauri_electron_unreal_and_capacitor_are_investigation_only() -> None:
    markers = {
        "src-tauri/tauri.conf.json": "PROJECT-SIGNAL-TAURI",
        "electron-builder.yml": "PROJECT-SIGNAL-ELECTRON",
        "fixture.uproject": "PROJECT-SIGNAL-UNREAL",
        "capacitor.config.json": "PROJECT-SIGNAL-CAPACITOR",
    }
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-native-signals-") as temp:
        project = Path(temp)
        for relative in markers:
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
        result = _compile({}, project)
    ids = {item["id"] for item in result["investigations"]}
    assert set(markers.values()) <= ids
    for fact in result["canonical"]["projectSignals"]:
        if fact["signal"] in {"Tauri", "Electron", "Unreal", "Capacitor"}:
            assert fact["adapter"] is None


def test_mcp_is_never_treated_as_a_runtime_adapter() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-mcp-") as temp:
        project = Path(temp)
        result = _compile({"runtimeAdapters": ["mcp", "browser-runtime"]}, project)
    assert "mcp" not in _adapters(result)
    unresolved = [item for item in result["investigations"] if item["id"] == "ADAPTER-UNRESOLVED"]
    assert any(item.get("details", {}).get("adapter") == "mcp" for item in unresolved)
    assert result["canonical"]["canApprove"] is False


def test_adapter_choice_is_content_bound_in_rule_set_hash() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-adapter-hash-") as temp:
        project = Path(temp)
        generic = _compile({"runtimeAdapters": ["generic-command"]}, project)
        browser = _compile({"runtimeAdapters": ["browser-runtime"]}, project)
    assert generic["canonicalSha256"] != browser["canonicalSha256"]


def test_generic_command_cannot_self_declare_ui_runtime_proof() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-adapter-boundary-") as temp:
        project = Path(temp)
        result = _compile({
            "primaryExperience": "INTERACTIVE_APPLICATION",
            "capabilityDomains": ["USER_INTERFACE"],
            "runtimeAdapters": ["generic-command"],
        }, project)
    required_rules = [item["id"] for item in result["canonical"]["layers"]]
    required_capabilities = sorted({
        capability
        for item in result["canonical"]["layers"]
        for capability in item["rule"].get("caseCapabilities", [])
    })
    case = {
        "id": "CASE-SPOOFED-UI",
        "satisfiesRuleIds": required_rules,
        "capabilities": required_capabilities,
        "adapter": {"id": "generic-command"},
    }
    check = coverage_check(result, [case])
    assert check["status"] == "FAIL"
    unsupported = check["details"]["unsupportedCapabilities"]
    assert unsupported == [{"caseId": "CASE-SPOOFED-UI", "adapterId": "generic-command", "capabilities": ["ui-runtime-interaction"]}]


TESTS = [
    test_versioned_adapter_descriptors_have_explicit_proof_limits,
    test_browser_evidence_cannot_prove_native_shell_or_target_hardware,
    test_godot_descriptor_binds_runtime_and_limits_headless_proof,
    test_tauri_electron_unreal_and_capacitor_are_investigation_only,
    test_mcp_is_never_treated_as_a_runtime_adapter,
    test_adapter_choice_is_content_bound_in_rule_set_hash,
    test_generic_command_cannot_self_declare_ui_runtime_proof,
]


def main() -> int:
    results = []
    for test in TESTS:
        try:
            test()
            results.append({"test": test.__name__, "status": "PASS"})
        except Exception as exc:
            results.append({"test": test.__name__, "status": "FAIL", "error": str(exc)})
    ok = all(item["status"] == "PASS" for item in results)
    print(json.dumps({"status": "PASS" if ok else "FAIL", "suite": "v3-adapters", "tests": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
