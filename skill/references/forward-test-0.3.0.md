# 0.3.0 forward-test contract

Forward tests use fresh temporary detached clones and never modify source projects.

- `goro-study_Github`: discover Godot and MCP signals; do not infer release intent or export readiness.
- `Game_HumenFire`: separate Web/Capacitor runtime, game/UI Profile AND composition, target environments, and TapTap distribution metadata; do not infer signed-device release readiness.
- `Pulse`: discover interactive UI, browser fallback, Tauri, backend, and hardware integration; Browser evidence must not prove Tauri, installer, Surface Go, GPU, or target-device behavior.

Every run records source HEAD/status before and after, clone path, discovered facts, proposed positioning, rules, warnings, investigations, and proof limits. It is diagnostic product-discovery evidence, not release acceptance.

## 2026-07-26 execution

The locked real-project run is recorded in `forward-test-0.3.0-results.json`.

- All three source repositories retained byte-identical Git status output and the same HEAD before/after the run.
- All resolvers ran in fresh detached clones and left those clones clean.
- No resolver inferred `releaseIntent` from repository contents.
- Godot activated `game AND ui-desktop` plus `godot-runtime`.
- The Web/Capacitor game activated `game AND ui-desktop`; Browser was covered, while Capacitor remained an explicit investigation.
- Pulse activated `backend-api AND ui-desktop`; Browser was requested from confirmed runtime targeting, while Tauri remained an explicit investigation.
- Pulse had 99 pre-existing working-tree entries. They were preserved and intentionally excluded from the committed-HEAD clone, so this run does not characterize those uncommitted changes.

The run is `PASS` only for conservative discovery/routing and source non-mutation. It is not bootstrap approval, project acceptance, or release evidence for any of the three projects.
