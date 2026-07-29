#!/usr/bin/env python3
"""Proof-boundary regressions for the 0.3.6 runtime adapters."""
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
from vibe_runtime.controller import evidence_adapter_contract_matches, validate_adapter_case_contract  # noqa: E402
from vibe_runtime.common import ControlError, canonical_bytes, sha256_bytes  # noqa: E402


def _compile(spec: dict, project: Path) -> dict:
    return compile_positioning(spec, project, RUNTIME)


def _adapters(result: dict) -> dict[str, dict]:
    return {item.get("adapterId", item.get("id")): item for item in result["canonical"]["runtimeAdapters"]}


def test_versioned_adapter_descriptors_have_explicit_proof_limits() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-adapters-") as temp:
        result = _compile({"runtimeAdapters": ["generic-command", "browser-runtime", "browser-webgl-game-runtime", "godot-runtime"], "primaryExperience": "GAMEPLAY", "runtimeTargets": ["browser-webgl2"]}, Path(temp))
    adapters = _adapters(result)
    assert set(adapters) == {"generic-command", "browser-runtime", "browser-webgl-game-runtime", "godot-runtime"}, sorted(adapters)
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


def test_webgl_game_adapter_requires_explicit_gameplay_target() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-webgl-routing-") as temp:
        project = Path(temp)
        valid = _compile({"primaryExperience": "GAMEPLAY", "runtimeTargets": ["browser-webgl2"]}, project)
        wrong_experience = _compile({"primaryExperience": "INTERACTIVE_APPLICATION", "runtimeTargets": ["browser-webgl2"], "runtimeAdapters": ["browser-webgl-game-runtime"]}, project)
        wrong_target = _compile({"primaryExperience": "GAMEPLAY", "runtimeTargets": ["browser"], "runtimeAdapters": ["browser-webgl-game-runtime"]}, project)
        disguised_targets = [
            _compile({"primaryExperience": "GAMEPLAY", "runtimeTargets": [target]}, project)
            for target in ("not-webgl", "webgl-disabled")
        ]
    assert "browser-webgl-game-runtime" in _adapters(valid)
    for result in (wrong_experience, wrong_target):
        assert "browser-webgl-game-runtime" not in _adapters(result)
        assert "ADAPTER-POSITIONING-MISMATCH" in {item["id"] for item in result["investigations"]}
    assert all("browser-webgl-game-runtime" not in _adapters(result) for result in disguised_targets)


def test_webgl_game_adapter_closes_gameplay_without_native_overclaim() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-webgl-proof-") as temp:
        result = _compile({
            "primaryExperience": "GAMEPLAY",
            "capabilityDomains": ["USER_INTERFACE", "REALTIME_ENGINE"],
            "runtimeTargets": ["browser-webgl2"],
        }, Path(temp))
    rules = [item["id"] for item in result["canonical"]["layers"]]
    generic = {
        "id": "CASE-GENERIC", "satisfiesRuleIds": ["RULE-ADAPTER-GENERIC_COMMAND"],
        "capabilities": ["generic-command-execution"], "adapter": {"id": "generic-command"},
    }
    webgl = {
        "id": "CASE-WEBGL", "satisfiesRuleIds": [value for value in rules if value != "RULE-ADAPTER-GENERIC_COMMAND"],
        "capabilities": [
            "candidate-integrity", "failure-conservation", "browser-runtime-observation",
            "browser-webgl-gameplay-observation", "ui-runtime-interaction", "gameplay-vertical-slice",
        ],
        "adapter": {"id": "browser-webgl-game-runtime"},
    }
    assert coverage_check(result, [generic, webgl])["status"] == "PASS"
    descriptor = _adapters(result)["browser-webgl-game-runtime"]
    limits = " ".join(descriptor["doesNotProve"] + descriptor["degradationLimits"]).lower()
    assert "capacitor" in limits and "target device" in limits and "game feel" in limits


def test_playwright_adapter_contract_is_mode_driven_and_fail_closed() -> None:
    descriptor = {"localExecution": {"mode": "playwright"}}
    valid_commands = [
        ["playwright", "test"], ["pnpm", "exec", "playwright", "test"],
        ["npm", "exec", "--", "playwright", "test"], ["npx", "playwright", "test"],
        ["yarn", "playwright", "test"], ["bunx", "playwright", "test"],
    ]
    valid = {"command": valid_commands[1], "artifacts": [{"path": "out/report.json", "minBytes": 1}]}
    for command in valid_commands:
        validate_adapter_case_contract("CASE-VALID", {**valid, "command": command}, descriptor)
    mutations = [
        ({**valid, "artifacts": []}, "artifacts"),
        ({**valid, "command": ["pnpm", "run", "test:e2e"]}, "Playwright command"),
        ({**valid, "command": ["node", "not-playwright.js"]}, "Playwright command"),
        ({**valid, "command": ["cmd.exe", "/d", "/c", "echo NON_PLAYWRIGHT", "playwright"]}, "Playwright command"),
    ]
    for case, fragment in mutations:
        try:
            validate_adapter_case_contract("CASE-BAD", case, descriptor)
        except ControlError as exc:
            assert exc.check_id == "HC-ADAPTER-CAPABILITY" and fragment in exc.message
        else:
            raise AssertionError(f"mutation was accepted: {case}")


def test_evidence_binds_command_invocation_capabilities_and_each_artifact() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-evidence-binding-") as temp:
        result = _compile({"primaryExperience": "GAMEPLAY", "runtimeTargets": ["browser-webgl2"]}, Path(temp))
    descriptor = _adapters(result)["browser-webgl-game-runtime"]
    adapter = {"id": descriptor["id"], "version": descriptor["version"], "sha256": sha256_bytes(canonical_bytes(descriptor))}
    case = {
        "id": "CASE-WEBGL", "command": ["pnpm", "exec", "playwright", "test"], "adapter": adapter,
        "oracle": {"exitCode": 0, "stdoutContainsAll": [], "stderrContainsNone": []},
        "capabilities": ["candidate-integrity", "failure-conservation", "browser-webgl-gameplay-observation"],
        "artifacts": [{"path": "./out/report.json", "minBytes": 4}, {"path": "out/six-stage.png", "minBytes": 8}],
    }
    evidence = {
        "evidenceId": "evidence-1", "candidateCommit": "a" * 40, "caseId": case["id"], "adapter": adapter, "command": case["command"],
        "observation": "runtime-observed", "result": "PASS", "exitCode": 0,
        "capabilitiesObserved": case["capabilities"],
        "artifacts": [
            {"path": ".vibe-control/evidence/artifacts/evidence-1/out/report.json", "bytes": 4},
            {"path": ".vibe-control/evidence/artifacts/evidence-1/out/six-stage.png", "bytes": 8},
        ],
    }
    invocation = {
        "schemaVersion": "3.2", "candidateCommit": evidence["candidateCommit"], "caseId": case["id"],
        "adapter": adapter, "command": case["command"], "requestedArtifacts": case["artifacts"],
        "operation": "execute-locked-case", "executionRoot": "detached-candidate-worktree",
        "oracleObservation": {"expectedExitCode": 0, "observedExitCode": 0, "missingStdout": [], "forbiddenStderr": [], "artifactFailures": []},
    }
    assert evidence_adapter_contract_matches(evidence, case, descriptor, invocation)
    mutations = [
        ({**evidence, "command": ["cmd.exe", "/c", "exit", "0"]}, invocation),
        ({**evidence, "capabilitiesObserved": [*case["capabilities"], "game-feel"]}, invocation),
        (evidence, {**invocation, "candidateCommit": "b" * 40}),
        (evidence, {**invocation, "command": ["cmd.exe", "/c", "exit", "0"]}),
        (evidence, {**invocation, "requestedArtifacts": case["artifacts"][:-1]}),
        ({**evidence, "artifacts": evidence["artifacts"][:-1]}, invocation),
        ({**evidence, "artifacts": [evidence["artifacts"][1], evidence["artifacts"][0]]}, invocation),
        ({**evidence, "artifacts": [{**evidence["artifacts"][0], "bytes": 3}, evidence["artifacts"][1]]}, invocation),
        ({**evidence, "artifacts": [{**evidence["artifacts"][0], "path": ".vibe-control/evidence/artifacts/evidence-OLD/out/report.json"}, evidence["artifacts"][1]]}, invocation),
    ]
    assert all(not evidence_adapter_contract_matches(mutated_evidence, case, descriptor, mutated_invocation) for mutated_evidence, mutated_invocation in mutations)
    for invalid_path in ("../out/report.json", "out/../report.json", "/tmp/report.json", "C:\\tmp\\report.json", "C:report.json"):
        invalid_case = {**case, "artifacts": [{"path": invalid_path, "minBytes": 4}, case["artifacts"][1]]}
        invalid_invocation = {**invocation, "requestedArtifacts": invalid_case["artifacts"]}
        assert not evidence_adapter_contract_matches(evidence, invalid_case, descriptor, invalid_invocation), invalid_path


def test_nested_capacitor_and_named_playwright_configs_are_discovered_without_native_adapter() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-nested-signals-") as temp:
        project = Path(temp)
        (project / "configs").mkdir(parents=True)
        (project / "configs" / "playwright.mobile.config.cjs").write_text("module.exports = {};\n", encoding="utf-8")
        (project / "packages" / "mobile" / "native").mkdir(parents=True)
        (project / "packages" / "mobile" / "native" / "capacitor.config.ts").write_text("export default {};\n", encoding="utf-8")
        result = _compile({}, project)
    signals = {item["signal"]: item for item in result["canonical"]["projectSignals"]}
    assert signals["Browser"]["adapter"] == "browser-runtime"
    assert signals["Capacitor"]["adapter"] is None
    assert "PROJECT-SIGNAL-CAPACITOR" in {item["id"] for item in result["investigations"]}


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
    test_webgl_game_adapter_requires_explicit_gameplay_target,
    test_webgl_game_adapter_closes_gameplay_without_native_overclaim,
    test_playwright_adapter_contract_is_mode_driven_and_fail_closed,
    test_evidence_binds_command_invocation_capabilities_and_each_artifact,
    test_nested_capacitor_and_named_playwright_configs_are_discovered_without_native_adapter,
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
