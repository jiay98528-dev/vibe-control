# 任务控制：强边界、弱流程（Schema 3.2）

模型可以在已确认定位和合同内自由选择架构、算法、实现顺序、调试方式和任务拆分。控制面只锁定会导致项目跑偏、证据失真或声明越界的边界。

## 风险自适应

- `R0`：只读调查；记录范围和证据，不创建候选。
- `R1`：精简任务卡、既有事实源导出的机械 case、负责会话复核。
- `R2`：完整合同、外部可观察 case、候选冻结、独立只读审核。
- `R3`：在 R2 上增加当前显式授权、恢复方案与执行/审核分离。

以下变化必须暂停并回到边界决定：目标或非目标、允许路径、事实源、产品语义、checkpoint/assertion、case/oracle、风险、外部状态、权限、不可逆性、声明上限，或已锁定 positioning/Profile/adapter/Skill/overlay。定位变化不得藏在普通任务合同中；使用 `reposition`。关键目标变化使用 `revise-objectives`，并使 task 及全部下游对象失效。

主线程在接纳 blocker、规划修复、修改架构/case/oracle、验收或交接前必须重新读取当前 `KEY_OBJECTIVES.md`。任务合同的 `objectiveRefs[]` 必须引用当前目标锁中的 `KO-*` 或 `KF-*`；未知 ID、旧修订或目标哈希漂移均 fail-closed。

## 项目定位、发行意图与任务是独立轴

`deliveryObjective` 描述当前里程碑，`releaseIntent` 描述预期交付边界，task `risk` 描述本次操作影响，task `maxClaimLevel` 描述本次任务声明上限。控制器取 phase、task、required cases、规则集和 release intent 上限的最小值，任何一轴都不能被另一轴代偿。

| `releaseIntent` | 任务路径 | 强制边界 |
| --- | --- | --- |
| `LOCAL_EXPERIMENT` | 可运行 R1–R3 调查或实现，但最高只到 `VERIFIED`。 | 不得 `accept` 为可交付状态；`release-check` 固定阻断。 |
| `PRIVATE_OPERATION` | 可到 `ACCEPTED`。R2/R3 需要独立审核和候选范围绑定的 owner 决定。 | R3 仍需当前授权、恢复方案和执行/审核分离；不要求 Ed25519、外部 release audit 或 receipt。 |
| `EXTERNAL_RELEASE` | 普通任务可按自身上限到 `VERIFIED/ACCEPTED`。 | 只有 `risk=R3` 且合同/case 允许 `RELEASE_READY` 的正式发行任务进入四角色签名链。 |

Skill 的安装、更新、Git commit 或 tag 不是项目 `EXTERNAL_RELEASE`，不得触发项目私钥要求。私钥从不由控制器创建、保存或索取；只有外部 R3 项目发行路径在治理锁中登记公钥并验证外部签名。

## 规则派生与 case 覆盖

`lock-task` 必须重新读取并验证 `project-positioning.json`、`resolved-rule-set.json` 和治理锁，而不是信任合同自报。它从当前规则集派生：

- `applicableRuleIds[]`：本任务适用的全部规则；
- `requiredCaseCapabilities[]`：这些规则要求的外部可观察能力；
- Profile AND、adapter 和 required/advisory Skill bindings。

合同只能增加限制或缩小工作范围，不能删除、覆盖或降低派生值。每个 applicable rule 必须由 required case 的 `satisfiesRuleIds[]` 覆盖；总 case 数、测试文件名或一个万能 case 不能替代逐规则覆盖。缺口由 `HC-RULE-CASE-COVERAGE` 阻断 lock。

Adapter 只可支持 descriptor 明确列出的证据能力：generic command 不证明真实 UI/部署，Browser 不证明原生壳/安装包/目标硬件，Godot headless 不证明渲染玩法或游戏感。Tauri、Electron、Unreal 与 Capacitor 在 0.3.5 只能形成 investigation。MCP transcript 按外部证据导入，不由 runtime 自报为本地执行。

Required Skill 缺失、不可寻址或 tree hash 漂移会阻断其任务；advisory Skill 缺失只产生 warning。安装 required Skill 必须获得当前人工批准，安装后重新发现、哈希和解析；所有 Skill 均 `canApprove=false`。

## R1 精简任务卡

使用精简 task-contract 模板。必须固定目标、可观察成功标准、允许/禁止路径、权威引用、机械 case、检查点、审计停止条件和最大声明。自动 case 必须逐项说明它来自哪条已锁定事实或 rule ID；不能追溯的 case 只能是草案。

## R2/R3 完整合同

使用完整 task-contract 模板，先闭合一个真实纵向切片：

- 真实输入或经授权 fixture；
- 核心状态变化；
- 用户或外部系统可见输出；
- 至少一个关键故障；
- 恢复、回退或明确失败信号。

用户确认产品事实、关键 case 和声明范围。实现者不得在同一执行任务中改写 required cases、oracle、阈值或规则目录；确需变化时结束任务、重新定位/锁定，并失效下游证据。

## 检查点合同

`acceptanceCheckpoints[]` 是 task 的预设通过条件，而不是 reviewer 临场生成的清单。每个 success signal 在当前 task 中恰好映射一次；`ACCEPTED`-capable task 的每个人工质量门恰好映射一个 `HUMAN` checkpoint。每个 required case 至少映射一次，自动 checkpoint 必须有 case 和 assertion。详细结构、确认哈希和迁移规则见 [checkpoint-contract.md](checkpoint-contract.md)。

用户在任务开始前一次确认规范化 checkpoint 摘要。`checkpointSetSha256` 随 task lock、candidate、evidence、review、owner decision 和 handoff 传播；checkpoint、assertion、case、oracle 或确认记录变化会使全部下游对象失效。机器不解释自由文本质量，只核对锁定结构与观察结果。

## 审计发现接纳

审核发现只能归入 `CURRENT_GOAL_DEFECT | MINIMUM_CORE_VIOLATION | SAFETY_OVERRIDE | HUMAN_DECISION | PROCESS_WARNING | INVESTIGATION | FUTURE_PROPOSAL | OUT_OF_SCOPE`。只有前三类可直接阻断；`HUMAN_DECISION` 只阻断其 `affectedClaims[]`，其余分类不得因严重度标签被自动升级。

`CURRENT_GOAL_DEFECT` 必须引用当前 task checkpoint，且其 objective 属于当前 task；项目存在但 task 未引用的宽泛目标不能成为 blocker。`MINIMUM_CORE_VIOLATION` 必须引用固定 core control 与非空证据；`SAFETY_OVERRIDE` 必须引用已锁定 `KF-*`。`affectedClaims[]` 必须向上闭合，并且 finding 只阻断明确列出的声明；仅影响 `RELEASE_READY` 的 finding 不得阻断 `VERIFIED`。

固定审核模式为 `CONFORMANCE_PLUS_BOUNDED_EXPLORATION`。所有 required checkpoint 报告闭合后必须停止；普通 warning/investigation/future/out-of-scope 每个候选最多三项，第四项由 `HC-AUDIT-EXPLORATION-BUDGET` 拒绝。真实当前目标、最低核心与安全越界不占数量预算，但仍需通过上述 admission。没有新候选、合同变化或用户授权不得重复开放式审核。

探索预算超限会产生候选绑定的 `reviews/audit-closures/<candidate-id>.json`。该记录使同一候选的后续开放式 audit 命中 `HC-AUDIT-STOP-CLOSURE`；失败 review 不会因未进入正式 `reviews/*.json` 而获得一套新预算。普通任务不得删除或改写该记录。

## 启动与恢复

`start`/`resume` 是 Skill 工作流名称，不伪装成 CLI。负责上下文从项目机器对象恢复，不从聊天摘要猜状态：先验证治理锁、定位、规则集、task lock、candidate 与证据，只恢复未完成动作。存在漂移时由固定失效边回退。

需要多个执行上下文时，先按 [multi-session-routing.md](multi-session-routing.md) 从实际工具解析 `CODEX_THREADS | SUBAGENTS | SERIAL`。创建用户可见的 Codex 任务只请求一次任务级授权；非 Codex 宿主显式降级为子智能体，既无 thread 也无 subagent 工具时串行执行。每个任务包写明目标、输入、允许文件、禁止文件、基线、验收命令、停止条件和机器回报格式。Skill 不设置 worker/子智能体固定数量上限；宿主容量、所有权和隔离仍是硬边界。Worker 和启发式 reviewer 可以生产证据或建议，但不能批准候选。

## 冻结与整合

负责会话先复核 worker diff、无关改动和文件所有权，再运行与风险相称的测试。正式冻结要求 Git、完整 commit/tree 和冻结时干净工作树。Candidate 必须直接内容绑定当前 positioning、resolved rule set、task lock、checkpoint set、合同、case/oracle、权威文件和输入。

冻结后产品、合同、case、oracle、定位、规则目录、Profile、adapter、Skill binding、依赖或环境发生变化，都必须按固定失效边使对应 execution、review、decision、receipt 和 handoff 失效。状态文件不得自报“仍然有效”。

并行 worker 的提交由负责会话整合。不得自动 push、发布、解锁里程碑、安装缺失 Skill 或执行 R3 操作。

## 重定位

`reposition --plan --spec <positioning-spec>` 只读展示规范化变化、规则变化和失效集合。`reposition --apply <plan-hash>` 必须匹配当前计划和授权；应用后重新编译规则并回到 `DRAFT/DIAGNOSTIC`。旧 task/candidate/evidence 只保留为诊断历史，不得继承 PASS。
