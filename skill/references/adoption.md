# Adopt、Reposition、目标修订与 Schema 迁移

## 第一轮必须只读

先在用户缓存初始化 progress ledger 和预任务仪表台，再盘点而不修改：Git 根、工作树、分支、子产品、元指令、产品/设计/架构文档、状态文件、case/oracle、候选、证据、交接和发布记录。本机仪表台不属于项目写入。

输出：

- 治理单元候选；
- 权威文档候选及哈希/跟踪状态；
- `AGENTS.md` 与 `CLAUDE.md` 的角色和冲突候选；
- 仓库可观察到的 runtime、环境和渠道信号；
- 当前工作树和候选映射；
- 当前控制面 Schema 版本与固定 runtime；
- 可机械验证的缺口；
- warning 与 investigation；
- 拟新增文件、拟修改文件及逐文件风险；
- 唯一下一项人工决定。

自然语言冲突只能提示并引用证据，不能用关键词匹配自动裁决权威关系。仓库发现只能形成 `discovered` 事实，不能推断 `primaryExperience`、`deliveryObjective`、`releaseIntent`、目标环境、发行渠道或主观质量标准。

## 未治理项目：按 Schema 4.0 新接入

接入前先让需求事实源成为受版本管理的普通文件，据此推导根级 `KEY_OBJECTIVES.md`，再使用 [project-positioning.md](project-positioning.md) 分轴确认项目：一个主体验、全部能力域、当前交付目标、发行意图、runtime targets、target environments、distribution channels、首个纵向切片、人工质量门禁和非目标。

必须分别询问并锁定 `deliveryObjective` 与 `releaseIntent`，最后用一次综合确认同时锁定关键目标摘要和规范化定位摘要。已有文档中的“部署”“发布”“内部使用”等文字只能作为问题背景；不得替用户选择 `LOCAL_EXPERIMENT`、`PRIVATE_OPERATION` 或 `EXTERNAL_RELEASE`。定位确认后，按 [checkpoint-contract.md](checkpoint-contract.md) 将 success signal、case/oracle 和 human gate 转为一次确认的 checkpoint 摘要，之后才能锁定首个 task。

运行只读 `resolve-rules` 展示 Profile AND、adapter 证明边界、Skill bindings、人工门禁、warning、investigation、冲突和安装请求：

- 0.4.0 实现 `generic-command`、`browser-runtime`、`browser-webgl-game-runtime`、`godot-runtime`；其中 WebGL 游戏 Adapter 只在已确认 `GAMEPLAY` 与显式 WebGL target 同时成立时激活；
- Tauri、Electron、Unreal 与 Capacitor 信号只产生 investigation；
- required Skill 缺失或漂移会阻断对应任务，advisory 缺失只告警；
- 缺失 Skill 的安装必须获得用户明确批准，安装后重新发现、哈希并解析；安装不使用私钥。

接入获批前先运行 `scripts/validate_installation.py`。接入后创建全新目标锁、positioning、默认本地 automation policy、规则集、治理锁、case catalog、状态与固定 runtime，不创建 candidate、evidence、review、decision 或 handoff 占位文件。状态从 `DRAFT/DIAGNOSTIC` 开始。Git 根、Git 子目录和无 Git portable copy 都可作为 `DEVELOPMENT` 来源，但必须登记实际 `sourceKind`，且最高只到 `DEVELOPMENT_CHECKED`；`SEALED` 包还必须闭合 Git/tag/包级审计收据。现有状态叙述只作为输入，不迁移为机器 PASS。

## 已有 Schema 4.0 项目：使用 Revise Objectives

目标、必须防止的失败模式、非目标、最低安全核心或治理成本预算变化时，不直接修改 `KEY_OBJECTIVES.md` 后继续开发。先运行 `revise-objectives --plan --spec <revision-spec>`，让控制器展示需求来源、目标哈希、ID 变化和下游失效集合；用户确认后再用精确 plan hash apply。应用后 task、candidate、evidence、review、decision 和 handoff 失效，状态回到 `DRAFT/DIAGNOSTIC`。

`AGENTS.md` 和 `CLAUDE.md` 都保留各自的项目事实，但追加相同的受管薄区块，指向 `.vibe-control/project-governance-lock.json` 与 `.vibe-control/stage-state.json`。不得复制完整控制规则、定位或发行状态形成第二事实源。已有文件只能在展示 diff 并获批后修改。

已有脏工作树可以完成只读接入设计，但不能形成正式候选。保留用户未提交改动，不整理或回滚。

## 已有 Schema 4.0 项目：使用 Reposition

## Schema 3.2：升级到 4.0

首次由 0.4.0 `adopt/resume` 接管时生成内容绑定升级计划。计划完整列出旧控制对象、目标/定位、当前 Git HEAD、默认自动策略、归档 manifest 和失效集合。脏工作树或无法无损解析时只报告阻断，不写产品或控制面。

Apply 在 staging 中验证后原子替换：旧 task、candidate、evidence、review、decision、receipt 和 handoff 只作为哈希归档，不继承 PASS；新控制面使用 `AUTO_LOCAL_TO_REVIEW / MILESTONE_COMMITS / NONE` 并回到 `DRAFT / BLOCKED / DIAGNOSTIC`。产品文件不得改变。详细对象见 [schema-guide.md](schema-guide.md)。

改变里程碑、主体验、能力域、runtime、目标环境、发行渠道、首个纵向切片、人工门禁或发行意图时，不直接改 JSON，也不通过新 task contract 覆盖旧值。

1. `reposition --plan --spec <positioning-spec>` 只读计算规范化差异、规则变化、风险、人工负担与全部失效对象；
2. 用户确认唯一定位变化和失效范围；
3. `reposition --apply <plan-hash>` 只接受当前精确计划；
4. 控制器重写定位、重新解析规则并回到 `DRAFT/DIAGNOSTIC`；
5. 旧 task、candidate、evidence、review、decision、receipt 与 handoff 仅保留为诊断历史。

## Schema 3.1：两阶段可恢复迁移

无 spec 的 `migrate --plan` 只读输出旧 success signal、human gate、case、内容派生的新 ID、oracle/artifact 转换、待补 checkpoint 和完整失效集合。主代理根据需求源草拟 checkpoint 映射，用户一次确认完整迁移摘要。带确认 spec 的 `--plan` 将旧控制面快照、spec 和确认记录绑定为计划哈希。

`migrate --apply <plan-hash> --spec <confirmed-spec>` 只在以下条件成立时执行：

- 工作树干净，spec 是受 Git 跟踪的普通文件；
- 当前旧对象、权威引用、spec 和计划哈希重新计算一致；
- staging 中的 3.2 对象、固定 runtime 与逐文件 archive manifest 全部验证通过；
- 原子替换成功，否则恢复旧 `.vibe-control`，不得留下半迁移目录。

旧控制面完整归档到 `.vibe-control/legacy/schema-3.1/<plan-hash>/control-plane/`。旧 task、candidate、evidence、review、decision、receipt 和 handoff 全部失效；新状态回到 `DRAFT / BLOCKED / DIAGNOSTIC`，必须重新确认 task checkpoint、lock、freeze 和 execute。迁移只转换可无损映射的 source ID、case oracle 和 artifact 结构，不替用户决定产品语义。

## Schema 2.0：只允许全新接入提案

0.4.0 **不迁移、不转换、不继承** Schema 2.0 的机器对象或证据。旧项目可以继续使用固定 0.2.2 runtime；一旦用 0.4.0 操作，必须返回 `VC-REINSTALL-REQUIRED` 且不写入。

用户批准可恢复归档后，重新运行 discovery、目标推导、positioning、checkpoint 确认、resolve-rules 和 Schema 4.0 bootstrap。旧证据不得通过复制、改 `schemaVersion`、重算哈希或 narrative summary 获得 4.0 资格。
