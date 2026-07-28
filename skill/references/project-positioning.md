# Project Positioning and dynamic rules

Use this route after requirements have produced a confirmed root `KEY_OBJECTIVES.md`, before the first Schema 3.2 bootstrap, and whenever the milestone, target environment, or distribution boundary changes.

## Separate axes

Never compress positioning into one product label. Lock these axes independently:

1. `primaryExperience`: gameplay, interactive application, service, or data/model system.
2. `capabilityDomains`: every material capability domain; applicable Profiles compose with AND.
3. `deliveryObjective`: prototype, demo, vertical slice, or production candidate for the current milestone.
4. `releaseIntent`: local experiment, private operation, or external release. Ask this separately and never infer it.
5. `runtimeTargets`, `targetEnvironments`, and `distributionChannels`: runtime, OS/device/architecture, and delivery channel are different facts.
6. `firstVerticalSlice`, `humanQualityGates`, and `nonGoals`: one observable outcome, its boundary, subjective decisions, and explicit exclusions. Each success signal and human gate is a `{id, statement}` object whose ID is derived from normalized text.

Repository inspection may only create `discovered` facts. A Playwright config, `project.godot`, Tauri, Capacitor, Electron, Unreal, an app-store file, or repository size never answers release intent or user acceptance.

## Resolution protocol

1. Create a bootstrap spec whose `keyObjectives` points to the tracked objective document, tracked requirement sources and tracked confirmation record. The same consolidated confirmation may bind both the canonical objective summary and the canonical positioning summary, while the hashes remain separate machine fields.
2. Run `resolve-rules`. It is read-only and reports Profile AND bindings, adapter proof limits, required/advisory Skills, human gates, warnings, investigations, conflicts, and install requests.
3. Resolve one boundary-changing question at a time. Required Skill installation needs explicit current approval; after installation, rediscover, hash, and resolve again. Advisory absence is only a warning.
4. Bootstrap only when `HC-POSITIONING-SCHEMA`, `HC-POSITIONING-CONFIRMED`, `HC-RULESET-CONFLICT`, `HC-RULESET-NON-WEAKENING`, `HC-ADAPTER-CAPABILITY`, `HC-SKILL-BINDING`, and `HC-RULE-CASE-COVERAGE` close.
5. The controller recompiles from sources. Never paste a preview in as authority.

Before accepting a blocker, planning a repair, changing architecture/cases/oracles, accepting a candidate, or handing off, reread the locked `KEY_OBJECTIVES.md`. A change to objectives uses `revise-objectives`; a change to positioning uses `reposition`. Neither may be hidden in a task contract.

## Proof boundaries

- `generic-command` proves only its exact candidate-bound command, transcript, counters, and declared artifacts.
- `browser-runtime` requires a real Playwright command plus artifacts. It cannot prove a native shell, installer, target hardware, or subjective visual quality.
- `godot-runtime` binds `project.godot`, the executable, and observed version. Headless smoke does not prove rendered gameplay or game feel.
- Tauri, Electron, Unreal, and Capacitor signals create investigations in 0.3.5. Covered browser/generic evidence may be reused only for what those adapters explicitly prove.
- MCP output is external evidence. It needs tool version, operation, raw transcript, artifact references, adapter identity, candidate, case, positioning, and rule-set bindings.

## Repositioning and legacy

`reposition --plan` is read-only and lists the exact change and invalidation set. Applying the exact plan hash archives downstream tasks, candidates, evidence, reviews, decisions, receipts, and handoffs as diagnostic history, then resets to `DRAFT / DIAGNOSTIC`.

After positioning is fixed, derive and once confirm the task checkpoint summary described in [checkpoint-contract.md](checkpoint-contract.md). Positioning confirmation does not itself approve a task or its human checkpoints.

Schema 2.0 is not a repositioning or 3.1→3.2 migration source. Return `VC-REINSTALL-REQUIRED`; do not convert or import its evidence. The user must first approve a recoverable archive and a fresh Schema 3.2 bootstrap.
