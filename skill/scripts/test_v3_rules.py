#!/usr/bin/env python3
"""Deterministic regressions for the Schema 3.2 six-layer rule compiler."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "assets" / "project-control" / "runtime"
sys.path.insert(0, str(RUNTIME))

from vibe_runtime.project_rules import LAYERS, canonical_rule_bytes, compile_positioning  # noqa: E402


def _compile(spec: dict, project: Path | None = None) -> dict:
    return compile_positioning(spec, project or ROOT, RUNTIME)


def _ids(result: dict) -> list[str]:
    return [item["id"] for item in result["canonical"]["layers"]]


def test_core_rules_are_unconditional() -> None:
    result = _compile({})
    assert not result["blockers"], result["blockers"]
    assert {"RULE-CORE-OBSERVABLE-CANDIDATE", "RULE-CORE-FAILURE-CONSERVATION"} <= set(_ids(result))


def test_six_layers_keep_fixed_order_and_only_add() -> None:
    layer_rules = {
        layer: [{"id": f"RULE-{layer}", "requirements": [f"keep {layer}"]}]
        for layer in LAYERS[:-1]
    }
    result = _compile({
        "layers": layer_rules,
        "projectOverlay": [{"op": "ADD", "rule": {"id": "RULE-PROJECT_OVERLAY", "requirements": ["extra"]}}],
    })
    assert not result["conflicts"], result["conflicts"]
    order = {name: index for index, name in enumerate(LAYERS)}
    observed = [order[item["layer"]] for item in result["canonical"]["layers"]]
    assert observed == sorted(observed), f"layer order drifted: {observed}"
    assert set(layer_rules) | {"PROJECT_OVERLAY"} <= {item["layer"] for item in result["canonical"]["layers"]}


def test_identical_id_deduplicates_but_conflicting_id_fails() -> None:
    shared = {"id": "RULE-SAME", "requirements": ["same"]}
    deduped = _compile({"layers": {"CORE": [shared], "EXPERIENCE": [dict(shared)]}})
    assert _ids(deduped).count("RULE-SAME") == 1
    assert not any(item["id"] == "RULE-ID-CONFLICT" for item in deduped["conflicts"])

    conflicted = _compile({"layers": {
        "CORE": [shared],
        "EXPERIENCE": [{"id": "RULE-SAME", "requirements": ["different"]}],
    }})
    assert "RULE-ID-CONFLICT" in {item["id"] for item in conflicted["conflicts"]}
    assert conflicted["canApprove"] is False


def test_overlay_remove_replace_and_lower_are_rejected() -> None:
    mutations = [
        {"op": "REMOVE", "rule": {"id": "RULE-X"}},
        {"op": "REPLACE", "rule": {"id": "RULE-X"}},
        {"op": "ADD", "rule": {"id": "RULE-X"}, "changes": {"evidenceLevel": "declared"}},
        {"op": "ADD", "rule": {"id": "RULE-X"}, "changes": {"maxClaimLevel": "RELEASE_READY"}},
    ]
    for mutation in mutations:
        result = _compile({"projectOverlay": [mutation]})
        assert "OVERLAY-NON-WEAKENING" in {item["id"] for item in result["conflicts"]}, mutation


def test_profiles_are_derived_and_composed_with_and() -> None:
    result = _compile({
        "primaryExperience": "GAMEPLAY",
        "capabilityDomains": ["REALTIME_ENGINE", "USER_INTERFACE"],
    })
    resolution = result["canonical"]["capabilityProfiles"]
    assert resolution["operator"] == "AND"
    assert set(resolution["requiredProfileIds"]) == {"game", "ui-desktop"}
    assert {"RULE-PROFILE-GAME-EXPERIENCE", "RULE-PROFILE-UI-INTERACTION"} <= set(_ids(result))
    assert resolution["canApprove"] is False


def test_compilation_is_byte_deterministic() -> None:
    left = {
        "runtimeAdapters": ["generic-command", "browser-runtime"],
        "layers": {"EXPERIENCE": [{"requirements": ["x"], "id": "RULE-X"}]},
    }
    right = {
        "layers": {"EXPERIENCE": [{"id": "RULE-X", "requirements": ["x"]}]},
        "runtimeAdapters": ["generic-command", "browser-runtime"],
    }
    first = _compile(left)
    second = _compile(right)
    assert first["canonicalSha256"] == second["canonicalSha256"]
    assert canonical_rule_bytes(first["canonical"]) == canonical_rule_bytes(second["canonical"])


def test_repository_discovery_never_infers_release_intent() -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-control-v3-discovery-") as temp:
        project = Path(temp)
        (project / "project.godot").write_text("[application]\n", encoding="utf-8")
        (project / "capacitor.config.ts").write_text("export default {};\n", encoding="utf-8")
        result = _compile({}, project)
        payload = canonical_rule_bytes(result).decode("utf-8")
        assert "releaseIntent" not in result["canonical"]
        assert not any(token in payload for token in ('"EXTERNAL_RELEASE"', '"PRIVATE_OPERATION"', '"LOCAL_EXPERIMENT"'))


TESTS = [
    test_core_rules_are_unconditional,
    test_six_layers_keep_fixed_order_and_only_add,
    test_identical_id_deduplicates_but_conflicting_id_fails,
    test_overlay_remove_replace_and_lower_are_rejected,
    test_profiles_are_derived_and_composed_with_and,
    test_compilation_is_byte_deterministic,
    test_repository_discovery_never_infers_release_intent,
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
    print(json.dumps({"status": "PASS" if ok else "FAIL", "suite": "v3-rules", "tests": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
