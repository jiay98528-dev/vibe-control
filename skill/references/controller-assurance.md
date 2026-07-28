# 控制器保证闭包

适用于开发或修改治理 Skill、状态控制器、证据校验器、候选冻结器、审核/批准门禁、发布声明器以及它们的 Schema 和测试工具。目标不是证明程序“看起来严谨”，而是让每条正式声明都能追溯到执行代码、对抗性反例和独立复核。

## 不可绕过原则

1. **公开承诺即攻击面**：SKILL、参考规范、Schema、模板和 CLI help 中的每个 MUST、支持命令和资格声明都要进入保证矩阵。
2. **未实现即阻断**：矩阵中任何适用于当前风险/阶段的义务为 `NOT_IMPLEMENTED`、`PARTIAL` 或缺失时，控制器必须 fail-closed；不得通过降低测试范围或改文案得到 eligible。
3. **Schema 必须被执行**：分发 Schema 但运行时不调用不是控制。对象通过 Schema 后仍要做跨对象、Git、时序和角色语义检查。
4. **跨对象闭包优先于单对象合法**：state、task、checkpoint set、candidate、case、evidence、review、decision、handoff 必须绑定同一候选和合同版本。
5. **逐 case 资格优先于总计数**：总 counters 只证明守恒；不能证明某个 case 真执行。每个 required case 必须有自己的 observation provenance 和原始记录绑定。
6. **资格由控制器派生**：阶段、health、claim 和前置事实由控制器计算或验证；状态文件不能同时提供输入与最终结论。
7. **外部反例不可被自带测试覆盖**：修复后的自测必须保留原始外部变异 case；独立复核使用未泄漏预期答案的新上下文。

## 保证矩阵

维护 [controller-assurance-matrix.json](controller-assurance-matrix.json)。每项义务至少包含：

- 稳定 ID 与来源 finding/规范；
- 风险和适用阶段；
- 精确的预期失败行为与退出协议；
- `implementationStatus`；
- 实际代码/Schema 引用；
- 正向与负向测试 ID；
- fail-closed 状态；
- 独立复核状态。

必需 obligation ID 集由 manifest builder、matrix validator 与 bundled runtime 的同名常量共同固定，并通过一致性测试。矩阵只能补充义务，不能通过删除既有 ID 缩小攻击面；缺少任一必需 ID 必须在包 maturity、静态验证和项目 runtime 三处 fail-closed。

实现状态闭包必须覆盖 `requirements` 与 `confirmedControls` 的并集。任一项目不是 `IMPLEMENTED` 时，manifest builder、matrix validator 与 bundled runtime 均不得产生 `FORMAL_GATE_READY`；不能只检查原始 finding 对应的 requirements。

包 manifest 必须直接消费 canonical static validator 的结构化结果，但只能声明 `CONTROL_IMPLEMENTATION_READY / AWAITING_EXTERNAL_VALIDATION`，不能产生 `FORMAL_GATE_READY`。正式包姿态由独立的 package release validator 聚合：当前 clean HEAD/tree、annotated release tag、annotated audit-bundle tree tag、重新计算后的 package/runtime inventory、matrix 哈希、审核者 actor/session、PASS 结果、P0/P1 计数、逐 case 命令/时间/退出码/counters、原始 transcript/artifact Git blob 与固定控制 ID 覆盖。runtime 只消费 bootstrap 物化并由治理锁绑定的包级审计收据。只在工作流中“建议运行 validator”、只哈希 manifest 文件本身、只封印叙述性报告或在矩阵中写布尔值均不构成强边界。

runtime 对顶层类型、数组 shape、非对象 item 与重复 ID 必须返回稳定 `HC-ASSURANCE-MATRIX-*` 检查，不得退化为 `CLI-INTERNAL-ERROR`。fail-closed 只回答“没有误放行”，稳定诊断还必须回答“具体为何阻断”。

manifest builder 对非法 JSON、顶层非 object 与 section 非 array 不得 traceback；它必须仍生成绑定实际坏输入的诊断 manifest。否则 runtime 只能看到陈旧 manifest 的哈希漂移，无法到达更具体的 TYPE/SHAPE 根因。

只有同时满足以下条件才能把义务改为 `IMPLEMENTED`：

1. 运行时真实消费相关对象或输入；
2. 至少一个正向 case 证明合法路径可用；
3. 至少一个最小负向变异证明绕过被拒绝；
4. 测试断言检查失败原因或稳定检查 ID，而不只检查非零退出；
5. 代码、Schema、测试和矩阵绑定同一版本；
6. P0/P1 或 R2/R3 义务取得独立只读复核。

## 修复流程

进入修复流程前，主线程必须重新读取当前 `KEY_OBJECTIVES.md`，包括已确认的信任边界与明确排除的威胁模型。外部发现先归类为 `CURRENT_GOAL_DEFECT`、`MINIMUM_CORE_VIOLATION`、`SAFETY_OVERRIDE`、`HUMAN_DECISION`、`PROCESS_WARNING`、`INVESTIGATION`、`FUTURE_PROPOSAL` 或 `OUT_OF_SCOPE`。当前目标缺陷必须引用当前 task checkpoint；最低核心违规必须引用固定 core control；安全越界必须引用 `KF-*`；`HUMAN_DECISION` 只阻断明确受影响声明。所有直接 finding 的 `affectedClaims[]` 必须向上闭合。严重度不能绕过任务/声明映射、扩大威胁模型或扩大当前任务。

### 1. 先做遏制

- 把当前能力降到真实可证明的声明等级。
- 对尚未实现的高阶段前置增加显式 `BLOCKED/NOT_IMPLEMENTED`。
- 保留诊断能力；不得继续产生误导性的 eligible。

### 2. 建立最小反例

- 从外部报告提取一个最小输入差异。
- 在修复前运行并确认它确实绕过；保存命令、退出码、stdout/stderr 和输入哈希。
- 反例不得依赖修改产品代码或降低其他门禁。

### 3. 建立执行追踪

- 从规范义务追到 Schema、解析器、跨对象检查和最终资格聚合点。
- 明确“谁消费该字段”“何时消费”“缺失时怎样失败”。
- 发现只有模板/目录/字段但没有消费代码时，标记 `NOT_IMPLEMENTED`。

### 4. 实现最窄修复

- 在资格产生前验证，而不是事后写 warning。
- 使用稳定 check ID 和结构化 JSON 错误。
- 对路径、身份、哈希、角色、时序和声明上限采用确定性比较。
- 对不可机器证明的独立性或产品判断，只验证所需人工记录存在且绑定正确，不伪造语义判断。

### 5. 跑四层测试

1. 单对象 Schema 正反例；
2. 跨对象闭包与状态机测试；
3. 外部变异回归与组合攻击；
4. 全量旧 fixture，防止修复破坏已有有效能力。

### 6. 独立复核

- 给审核者原始 Skill/运行时、规范、锁定 checkpoint 和未标注候选结果的测试入口。不得提供实现者摘要、已知缺陷或预期总裁决。
- 要求其从公开承诺重新派生攻击面，不能只重跑实现者给出的测试列表。
- 复核结果绑定精确 package manifest；代码、Schema 或测试变化后自动失效。
- 物化前运行 `scripts/check_audit_path.py`，用 source、candidate 和计划 audit root 计算最长受管相对路径及预计完整路径。超过 240 字符必须以 `VC-AUDIT-PATH-BUDGET / BLOCKED` 停止，改用 `C:\vc34\<id>` 一类短根，或由操作者对单条 Git 命令显式使用 `-c core.longpaths=true`；不得静默修改全局 Git 配置。
- 审核先逐项报告全部 required checkpoint，再使用最多三项普通探索发现。检查点闭合即满足 conformance stop condition；没有新候选、合同变化或用户授权时，不得为寻找更多流程问题而重复开放式审核。
- 候选必须由 fresh clone 的首次工作树物化。Windows 使用 `git -c core.autocrlf=true clone --no-local --branch <candidate-tag-or-branch> --single-branch <source> <audit-dir>`，随后确认 `HEAD` 等于被审精确 commit；不要使用空工作树的 `--no-checkout` 再首次 checkout，因为目标 commit 的 `.gitattributes` 不保证在同一次首次物化中先于受管文件生效。物化后立即运行 `scripts/build_manifest.py --verify`；Git clean 不能替代字节级校验。
- “先检出旧默认分支、再切换候选”不是可信的候选物化协议。Git 可能保留未变化文件的旧 CRLF 工作副本；若使用该路径，只有 manifest 仍实际 PASS 时才能继续，否则必须重新建立以候选为首次 checkout 的 fresh clone。按字节哈希的 transcript 使用 `-text` 禁止 checkout 转换，仓库 blob 本身固定为 LF。
- 长回归必须逐 case 输出实时进度、稳定结果 ID、非零总 counters，并对每个 case 设置超时。超时、runner 协议损坏或无最终守恒结果均为 FAIL，不得按“仍在运行”或历史自测推定通过。

### 7. 收口声明

- `quick_validate` 和 package manifest 只证明 Skill 结构与内容寻址。
- `DEVELOPMENT_DIAGNOSTIC` 可以安装并用于诊断开发，但最高只到 `DEVELOPMENT_CHECKED`；它不得被描述为正式封印、正式安装或 `FORMAL_GATE_READY`。正式资格与开发安装是两个不同概念。
- baseline fixtures 只证明已覆盖行为。
- formal-gate readiness 只能在保证矩阵闭合、变异套件全绿和外部复核完成后声明。
- 激活采用“内容候选 → 独立审计证据 bundle tree → 两个 annotated tag → seal 复核”协议。候选 tree 完成 manifest 后不得再改；`vibe-control-audit/v<version>` 指向包含 `report.json`、`evidence-manifest.json`、逐 case transcript 与声明 artifact 的 Git tree，`v<version>` 指向候选 commit 且其 JSON message 绑定 bundle/report/evidence 对象、commit/tree 与三项内容哈希。矩阵保持 `formalClaimsAllowed=false`。validator 必须重算当前 package/runtime inventory，并验证 case 命令、时间、退出码、非零守恒 counters、零 skip、blob/哈希和控制覆盖；任何审计后代码、Schema、测试、文档、manifest 或证据变化都自动失效。
- package validator 必须聚合所有当前可独立检查的前置，如 release tag、audit tag、inventory 和工作树状态；不得因第一个缺失项短路后把它描述为“唯一 blocker”。报告对缺陷范围只能写“本轮覆盖内未发现 P0/P1/P2”，不能外推为系统不存在未知缺陷。

## 测试设计规则

- 每个 hard claim 至少有一个“只改变该义务”的负向变异。
- 检查失败必须命中预期 check ID；被其他无关门禁挡住不算该义务测试通过。
- 对顺序流程测试缺步骤、错顺序、重复步骤、过期步骤和跨候选拼接。
- 对引用测试缺失、错 ID、旧哈希、跨任务、路径逃逸和未跟踪文件。
- 对 evidence 测试自报 case、伪 transcript、纯 `declared/derived`、时间倒序、结果/计数冲突和一份执行冒充多个 case。
- 对 checkpoint 测试遗漏、重复、未知 ID、确认哈希漂移、case/assertion 越界、伪造 review result、人工门缺项/拒绝以及第四项普通探索发现。
- 对高阶段测试无审核、自审自批、无人工批准、批准范围不符、批准过期和候选变化后沿用批准。
- 对 CLI 测试未知命令、缺参数、错误类型、畸形 JSON 和异常；stdout 必须保持协议稳定。

## 停止条件

出现以下任一情况时停止正式收口并保持 DIAGNOSTIC：

- 规范义务没有矩阵项；
- 矩阵声称 implemented 但没有真实消费代码；
- 负向测试只因无关检查失败；
- 测试预期写入生产逻辑或审核者收到预期答案；
- P0/P1 反例仍可产生 eligible；
- package、runtime、Schema、测试或外部复核不绑定同一版本。
- 候选由旧工作树切换得到且 package manifest 未重新验证；
- 对抗回归没有逐 case 超时、实时进度或最终 counters。
