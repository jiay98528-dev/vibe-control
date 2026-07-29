# Bootstrap：以 Schema 3.2 建立项目控制面

Bootstrap 的目标不是套用一个粗糙“项目类型”，而是先把产品边界拆成可确认、可寻址的定位轴，再由控制器编译唯一规则集。详细字段、确认和重定位协议见 [project-positioning.md](project-positioning.md)。

## 1. 只读发现

先运行 `inspect`，确认目录是否为空、是否存在 Git、是否包含多个可独立发布单元、是否已有 `AGENTS.md`、`CLAUDE.md` 或产品文档。检测到多个治理单元时先确认范围，不得默认 repo 根或当前目录。

发现结果只能写入 `discovered` 事实。仓库名称、框架、配置文件、体量、现有部署文字或用户数量不能替用户选择 `primaryExperience`、`deliveryObjective`、`releaseIntent`、自动化模式、目标环境、发行渠道或主观质量门禁。发现 Tauri、Electron、Unreal 或 Capacitor 只能生成 investigation；0.3.7 不具备这些原生运行时的正式 adapter。

## 2. 自适应愿景与首个切片

逐轮补齐以下事实，每轮只问会改变边界的问题：

1. 谁在什么情境下使用产品，真实问题是什么；
2. 什么外部结果表示产品有用；
3. 明确非目标和本轮不做的内容；
4. 平台、硬件、部署、兼容与依赖约束；
5. 数据、安全、隐私、付费、发布或不可逆风险；同时明确当前信任的候选、执行环境与维护角色，不信任的输入/自报结果、本阶段不防御的攻击者能力，以及重开威胁模型的真实触发条件；
6. 已有事实源、参考实现和必须保留的接口；
7. 第一个纵向切片：真实输入、状态变化、外部输出、故障、恢复；
8. 哪些结果必须由用户体验或授权。

一句话需求不能满足上述条件。没有用户依据的功能、平台、技术栈、同步方式、数据策略、商业模式、搜索/导出/备份能力都必须写成 `UNKNOWN`，不得为了形成漂亮摘要而自行补全。每次只选择当前最影响下游的一个未知项提问；用户回答后再更新草案。

原子问题只能改变一个变量，禁止把“单人 + Windows + 本地文件 + 不同步”捆绑为一个选项。默认提问顺序为：用户可见结果/成功信号 → 第一个纵向切片 → 非目标 → 平台环境 → 数据与安全 → 主观质量；用户已明确的字段可跳过，不得为了形式逐项重问。技术栈始终晚于产品结果。

发现矛盾时明确指出，不用模型偏好替用户消解。字段稳定后生成临时愿景摘要，包含假设和仍未知项。用户明确确认摘要前不得写项目文件；需要持久化的产品简报只能在 bootstrap 获批后作为已声明的权威文件创建。

尚有未知边界时不得提前要求确认摘要；继续用原子问题补齐会改变边界的事实。事实稳定后只做一次综合确认，绑定需求摘要、关键目标、positioning 与三选一自动化模式；该轮不得同时引入新的平台、技术栈、范围或架构选择。`确认` 与 `修改` 都必须显示风险分、人工负担分和影响。

## 3. 推导并确认关键目标

愿景事实与需求来源落盘后，先从受版本管理的需求文档推导根级 `KEY_OBJECTIVES.md`，再进入定位与任务规划。目标文档必须包含一句 North Star、最多五个 `KO-*`、必须防止的 `KF-*`、明确的 `NG-*`、信任边界与威胁模型、最小安全与证据核心、治理成本预算、审计接纳规则、停止条件和变更规则。

机器只验证文档身份、Git 跟踪状态、SHA-256、修订号、ID 集合和引用闭包，不判断目标文字是否“合理”。用户用一次综合确认同时确认目标摘要与定位摘要；确认记录和规范化摘要哈希写入 bootstrap spec。普通 worker、执行者和审核者不得改写目标文档。

目标确认后，主线程在接纳 blocker、规划修复、修改架构/case/oracle、验收或交接前必须重新读取 `KEY_OBJECTIVES.md`。需要改变目标时只能运行 `revise-objectives --plan`，展示影响和失效集合并取得确认，再用精确 plan hash apply；不得把目标变化藏在普通任务里。

## 4. 分轴确认项目定位

不能用“Godot 游戏 Demo”之类单标签代替定位。以下轴必须分开收集并锁定：

- `primaryExperience`：`GAMEPLAY | INTERACTIVE_APPLICATION | SERVICE | DATA_OR_MODEL_SYSTEM`；
- `capabilityDomains[]`：所有实质能力域；适用 Profile 采用 AND，不允许只选一个主标签覆盖其他能力；
- `deliveryObjective`：`PROTOTYPE | DEMO | VERTICAL_SLICE | PRODUCTION_CANDIDATE`，只描述当前里程碑；
- `releaseIntent`：`LOCAL_EXPERIMENT | PRIVATE_OPERATION | EXTERNAL_RELEASE`，描述预期交付边界；
- `runtimeTargets[]`、`targetEnvironments[]`、`distributionChannels[]`：运行时、操作系统/设备/架构和发行渠道分别记录；
- `firstVerticalSlice.successSignals[]`、`humanQualityGates[]` 与 `nonGoals[]`；signal/gate 均为内容派生 ID 加 statement 的稳定对象。

`deliveryObjective` 与 `releaseIntent` 必须分别明确，不能通过一个粗糙单选项互相推断。目标与定位事实稳定后，以一次综合确认写入用户决定引用、关键目标摘要 SHA-256 与规范化定位摘要 SHA-256。任务只能缩小定位范围，不能通过合同改写或削弱项目规则。

### 发行意图是必答轴

在写 bootstrap spec、`.vibe-control/`、产品代码或任何受管文件之前，必须单独询问用户：

> 这个项目的预期发行状态是什么？它约束可达到的声明等级，不等于本 Skill 的安装、开源、付费、发布或 Git tag。

只展示以下三个互斥选项，不能代选、不能给模板默认值；每项都必须显示分数和影响。分数表示错误声明或控制缺失的风险与人工负担，**不是**任务的 R0–R3 风险评级：

| 用户选择 / 机器枚举 | 提示中的分数与影响 | 可达到的最高声明 / 路径 |
| --- | --- | --- |
| 本地实验 / `LOCAL_EXPERIMENT` | 风险 20/100；人工负担 10/100；影响：只做本机探索、原型或学习，不形成可交付运行声明。 | `VERIFIED`；允许事实验证和诊断；`release-check` 始终 `BLOCKED`。 |
| 私有运行 / `PRIVATE_OPERATION` | 风险 45/100；人工负担 35/100；影响：供本人或明确封闭的内部使用，需要候选绑定的独立审核和 owner 决定。 | `ACCEPTED`；R2/R3 保留人工授权、恢复和角色分离；不要求 Ed25519，也不进入 `RELEASE_READY`。 |
| 外部发行 / `EXTERNAL_RELEASE` | 风险 75/100；人工负担 70/100；影响：面向外部用户、生产交付或公开发行，正式发行须承受完整候选级审计链。 | `RELEASE_READY` 仅可由 R3 合同达到；要求受信公钥、签名执行/审核/receipt 链。 |

仅当用户已经明确交付对象时才推荐对应一项；若未说明，保持 `UNKNOWN/DRAFT` 并只追问这一题。不要因为项目小、只有一名开发者、暂时免费或 Skill 本地安装就改变发行意图。

将用户明确选择写入 Schema 3.2 bootstrap spec 的 positioning。缺失、未知或仅由发现结果推断的值必须被控制器拒绝。成功 bootstrap 后定位写入 `project-positioning.json`，由治理锁、规则集、task lock 和 candidate 形成内容闭包。改变任何定位轴必须使用 `reposition`，批准后回到 `DRAFT/DIAGNOSTIC` 并失效下游对象。

### 自动推进模式也是必答轴

在需求、关键目标、positioning 与发行意图已经稳定后，把自动化模式加入同一次启动综合确认。新项目不得默认代选，未回答时不得生成 bootstrap 控制面。只允许以下固定组合：

| 用户选择 / 机器枚举 | 提示中的分数与影响 | 固定权限组合 |
| --- | --- | --- |
| 本地自动推进 / `AUTO_LOCAL_TO_REVIEW`（推荐） | 风险 45/100；人工负担 20/100；影响：自动规划、委派、开发、验证并创建通过检查的里程碑提交，到人工复核点停止；不推送。 | `commitPolicy=MILESTONE_COMMITS`；`pushPolicy=NONE`。 |
| 自动推进并推送 / `AUTO_PUSH_TO_REVIEW` | 风险 60/100；人工负担 15/100；影响：增加向已存在且已绑定 upstream 的非强制里程碑推送；冲突、认证或远端身份变化立即停止。 | `commitPolicy=MILESTONE_COMMITS`；`pushPolicy=EXISTING_UPSTREAM_MILESTONES`。 |
| 逐阶段确认 / `MANUAL_STAGE_CONFIRMATION` | 风险 25/100；人工负担 65/100；影响：保留逐阶段人工确认，不自动提交或推送。 | `commitPolicy=MANUAL`；`pushPolicy=NONE`。 |

确认记录必须绑定精确项目、关键目标、positioning、模式、提交/推送权限、固定停止条件与规范化摘要哈希，并物化为 `.vibe-control/automation-policy.json`；治理锁与 task lock 引用同一策略身份。不得自由拼接第四种权限组合。

旧 Schema 3.2 项目没有策略时继续解释为 `MANUAL_STAGE_CONFIRMATION`，不得静默写入策略或取得自动副作用权限。加入或改变模式只能使用 `automation --spec <policy.json> --plan`，展示权限差异和失效集合后，再以精确 `--apply <plan-hash>` 应用；应用会归档当前 task、使下游对象失效并回到 `DRAFT / BLOCKED / DIAGNOSTIC`。详见 [automation-advancement.md](automation-advancement.md)。

## 5. 只读解析 Profile、Adapter 与 Skill

运行：

`python <skill-root>/scripts/vibe_control.py resolve-rules --project <root> --spec <bootstrap-spec>`

`resolve-rules` 必须只读，输出六层规则、Profile AND 结果、adapter 证明/非证明能力、required/advisory Skill bindings、人工门禁、warning、investigation、冲突和安装请求。它不得写 `.vibe-control/`，也不得把调用方提供的解析结果当作事实。

Profile 的选择由定位轴确定：`GAMEPLAY` 或 `REALTIME_ENGINE` 激活 `game`；`USER_INTERFACE` 激活 `ui-desktop`；`BACKEND_API` 激活 `backend-api`；`DATA_PIPELINE` 或 `LLM` 激活 `data-llm`。多项适用时全部生效。

0.3.7 有四种已实现 adapter descriptor：

- `generic-command`：只证明锁定命令、原始 transcript、counters 与明确产物；
- `browser-runtime`：证明真实浏览器/Playwright 运行及其截图、trace、日志和逐 case counters；
- `browser-webgl-game-runtime`：只在已确认 `GAMEPLAY` 与显式 WebGL target 同时成立时，以直接锁定的 `playwright test`、实际工具版本和执行前清理后生成的候选绑定产物证明浏览器玩法切片；浏览器、视口和 WebGL 细节仅在锁定产物记录时有效；
- `godot-runtime`：绑定 `project.godot`、Godot 可执行文件与精确版本，并在候选 detached worktree 中执行。

Browser 与 Browser WebGL 证据不能证明原生壳、安装包、目标设备、GPU/热稳定或主观游戏感；WebGL 玩法结论仍需锁定人工体验门。Godot headless 不能证明渲染玩法或游戏感。Tauri、Electron、Unreal 与 Capacitor 在本版只有发现和 investigation，不得被描述为已实现 adapter。MCP 结果只能按外部证据导入并绑定工具版本、操作、原始 transcript、adapter、candidate 与 case。

Skill binding 分为 `required` 和 `advisory`。required Skill 必须能绑定路径、版本和确定性 tree hash，缺失或漂移会阻断对应任务；advisory Skill 不可寻址或缺失时只产生 warning。所有 Skill 都必须 `canApprove=false`。安装计划不是授权：只有用户明确批准后才能从本地受管包或 Codex 策展/推荐源安装；随后必须重新发现、哈希并解析规则。Skill 安装、版本或 Git tag 不要求也不得索取私钥。

规则层只能增加约束。相同 ID 且内容一致可确定性去重；内容冲突、overlay 删除规则或降低 case/证据要求必须 fail-closed。相同输入必须生成字节一致的 `resolved-rule-set.json` 与 SHA-256。

## 6. 风险与任务边界

按固定七因子评分，给出 R0–R3 建议。Profile/adapter/Skill routing 不能替代任务风险，也不能提高 release intent 或 delivery objective 所允许的声明上限。R2/R3 或强制升级项必须由用户确认。

风险档位不得凭文字手算：`0–34=R1`、`35–69=R2`、`70–100=R3`，强制升级项为 R3。运行 `python <skill-root>/scripts/vibe_control.py risk --score <0-100> [--forced-r3]` 获取机械映射。

## 7. 生成批准规格

从 `assets/project-control/templates/bootstrap-spec.json` 生成临时 Schema 3.2 规格。规格至少绑定：

- 当前 Skill 版本、`package-manifest.json` SHA-256 与 package mode；`SEALED` 必须有已验证包级审计收据，`DEVELOPMENT` 必须明确限制在诊断开发；
- 已确认的 `KEY_OBJECTIVES.md`、需求来源、修订、ID 集合、确认记录和 SHA-256；
- 项目 ID、治理单元与完整 positioning；
- 用户确认记录及规范化定位摘要 SHA-256；
- 已确认的 automation policy 1.0：模式、固定 commit/push 组合、停止条件、确认记录和规范化摘要 SHA-256；
- 已确认愿景决策；
- 权威文件候选；
- 风险因子；
- case 草案及其 `satisfiesRuleIds[]`；
- Profile、adapter、Skill 与 overlay 输入；
- warning、investigation 和最多一个待人工决定事项。

临时规格不是项目状态，完成 bootstrap 后可以删除。为避免在控制面生成前额外制造一次仓库提交，默认把临时规格放在项目 Git 根之外；若选择放进项目内，就必须先把它连同已确认事实源提交，不能用脏工作树启动 bootstrap。`bootstrap` 必须重新校验 positioning、重新发现并编译规则，不得消费 `resolve-rules` 输出或调用方自报哈希作为权威。

Case 的 `capabilities[]` 不是自由声明。控制器必须把它与 adapter descriptor 的 `provesCaseCapabilities[]` 做闭包校验；超出 adapter 机器证明边界的能力以 `HC-RULE-CASE-COVERAGE` fail-closed。尤其禁止 `generic-command` 仅靠填写 `ui-runtime-interaction` 或 `gameplay-vertical-slice` 来冒充真实 UI/游戏运行证据。

`trustedKeys` 在本地实验和私有运行中保持空数组。只有已经选择 `EXTERNAL_RELEASE`、准备锁定 `R3/RELEASE_READY` 正式发行任务时，才登记 `executor`、`auditor`、`release-auditor`、`owner` 四类互不复用的**公钥**；任何私钥都不得进入 bootstrap spec、Skill、runtime 或项目目录。

## 8. 安全写入

只有定位与 automation policy 已明确确认、规则无冲突、required Skill 闭合且 case 能覆盖全部适用规则后才运行 `bootstrap`。新项目缺少自动化选择必须以 `HC-AUTOMATION-POLICY-REQUIRED` 阻断，且不得留下部分 `.vibe-control/`。成功写入时创建 `project-positioning.json`、`automation-policy.json`、`resolved-rule-set.json`、治理锁、case catalog、阶段状态与固定 runtime；只允许自动创建新文件。若 `AGENTS.md` 或 `CLAUDE.md` 已存在，脚本必须报告需要批准的托管区块，不得覆盖。`PRODUCT.md` 与 `ARCHITECTURE.md` 仅在确有需要且不存在时创建薄骨架；未知内容写 `TBD`，不得编造。

定位与规则结果使用稳定检查 ID：`HC-POSITIONING-SCHEMA`、`HC-POSITIONING-CONFIRMED`、`HC-RULESET-BINDING`、`HC-RULESET-CONFLICT`、`HC-RULESET-NON-WEAKENING`、`HC-RULE-CASE-COVERAGE`、`HC-ADAPTER-CAPABILITY`、`HC-SKILL-BINDING` 与 `VC-SKILL-INSTALL-APPROVAL`。

没有 Git 时先建议初始化。用户拒绝后仍可建立控制面，但必须保持 `DRAFT/DIAGNOSTIC`，不能冻结正式候选。

## 9. 预设检查点并一次确认

Bootstrap 只固定定位和 case，不替用户自动形成任务验收结论。首个 task 规划前，按 [checkpoint-contract.md](checkpoint-contract.md) 完成：

- 每个 success signal 恰好映射一个 checkpoint；
- `ACCEPTED` 任务的每个人工质量门恰好映射一个 `HUMAN` checkpoint；
- 每个 required case 至少被一个 checkpoint 使用，自动 checkpoint 具有 case 和 assertion；
- 写清 `expected` 与 `notProven`，让审核者知道通过事实和外推上限；
- 向用户一次展示规范化摘要，并将确认记录与 `checkpointSetSha256` 写入 task contract。

机器只验证 ID、结构、引用、哈希和 case/oracle 的实际结果，不替用户判断文字是否表达了正确产品目标。

## 10. 首个切片与继续开发

建立 R1 精简任务卡或 R2/R3 完整合同。合同的 `objectiveRefs[]` 必须至少引用当前目标锁中的一个 `KO-*` 或 `KF-*`，并包含已确认的 `acceptanceCheckpoints[]`、`checkpointConfirmation` 与固定 `auditPolicy`。`lock-task` 从已锁定规则集派生 `applicableRuleIds` 和 `requiredCaseCapabilities`，并绑定当前 automation policy；合同只能增加或缩小任务范围，不能删减它们。每个 required case 必须通过 `satisfiesRuleIds[]` 覆盖适用规则。R1 机械 case 只有能映射到已确认事实源时才可自动锁定；新增产品语义或 R2/R3 case 必须确认。

控制面和首个切片就绪后按已确认模式推进：手动模式继续询问是否开始写产品代码并逐阶段确认；自动模式不再在普通阶段停下来询问，负责上下文可在检查通过后创建里程碑提交。只有 `AUTO_PUSH_TO_REVIEW` 可向精确绑定的既有 upstream 非强制推送。全部自动模式都必须在候选闭合、`HUMAN` checkpoint/owner decision、边界变化、R3/不可逆操作、硬失败、推送冲突或用户中断时停止并生成外部缓存 Dashboard。
