# 证据、硬检查与非硬发现

> `vibe-control 0.3.6` 的正式声明先受定位锁中的 `releaseIntent` 限定：`LOCAL_EXPERIMENT` 最高 `VERIFIED`，`PRIVATE_OPERATION` 最高 `ACCEPTED`，`EXTERNAL_RELEASE` 才可能到 `RELEASE_READY`。任何路径还要求项目锁绑定有效的 Skill 包级审计收据、`project-positioning.json`、`resolved-rule-set.json` 与已确认 checkpoint set；外部 R3 正式发行另需绑定当前项目候选、owner decision、已签 external-release-audit 与当前 package/runtime/matrix 的项目级 release receipt。缺失、重放、漂移、失败结果或无效的**必需**签名一律 fail-closed。

Release receipt 不是包级“曾审过”的通行证，也不能单独授权任何项目候选。R2 与私有 R3 的 review/owner decision 是受版本管理、候选绑定的人工记录，不要求密码学身份；真实性仍由人负责。只有 `EXTERNAL_RELEASE + risk=R3 + maxClaimLevel=RELEASE_READY` 的任务要求 `executor`、`auditor`、`release-auditor`、`owner` 的 Ed25519 公钥与签名闭包，这些 actor/公钥不得复用，release-auditor 还必须与内部 review auditor 分离。私钥不得写入 runtime、Skill 或项目受管目录，也不用于 Skill 安装、授权、收费或本地 tag。

本策略的目的，是限制无证据的声明，而不是自动判定产品质量。校验器只检查结构、绑定和失效关系；它不把 JSON、测试名称或模型自报当作真实执行。

## 最小完整硬检查集

以下检查可机械判定，失败时只能阻止相关声明或状态跃迁：

1. **文件完整性**：受管证据、产物、合同、case 和候选清单使用安全相对路径与安全标识符；拒绝绝对路径、路径穿越和符号链接逃逸；声明的字节数与 SHA-256 匹配。
2. **引用闭包**：ID、路径、哈希、candidate、case、transcript、产物与审核记录均存在且互相可解析；拒绝孤儿引用、重复 ID 与混候选引用。
3. **目标、定位与规则闭包**：关键目标对象绑定受跟踪文档、需求来源、确认摘要、修订、ID 集合和 SHA-256；定位对象满足 Schema 且具备用户确认摘要哈希；规则集由六层输入确定性编译；目标引用未知、规则 ID 冲突、overlay 弱化、治理锁/任务/候选哈希漂移或建立第二规则状态源一律失败。
4. **规则覆盖**：`lock-task` 派生的每个 `applicableRuleId` 和 `requiredCaseCapability` 都由 required case 的 `satisfiesRuleIds[]` 与能力字段覆盖；任务合同、Profile、adapter 或 Skill 不能删减覆盖要求。
5. **候选绑定**：候选精确绑定 commit/tree、positioning、resolved rule set、task lock 和 `checkpointSetSha256`；正式候选工作树洁净；证据、case、oracle、assertion、受测输入和产物均绑定同一候选或明确的上游版本。
6. **执行守恒**：每个 required case 有可解析执行记录、退出码、时间、非空 counters、原始 transcript 及哈希；`executed > 0`、`skip == 0`，并且 passed/failed/skipped 与总计守恒。一份执行不能冒充多个无独立 provenance 的 case。
7. **Adapter 能力**：执行或导入记录必须绑定当前 adapter descriptor、工具/可执行文件版本、操作和输入；证据只能满足 descriptor 明确声明的能力，任何明确的 non-proof 均不得外推为 PASS。
8. **Skill binding**：required Skill 的路径、版本和确定性 tree hash 必须闭合且 `canApprove=false`；advisory Skill 不得成为硬 PASS 的唯一来源。未批准的安装请求不能通过写状态文件消除。
9. **覆盖与声明**：required case 完整覆盖；声明等级不得超过 phase、合同、case、规则集与项目 `releaseIntent` 的最小上限。
10. **失效传播**：关键目标/需求来源、定位、规则目录、Profile、adapter、Skill binding、overlay、合同、checkpoint/assertion/确认记录、case、oracle、候选、产品输入或环境锁发生绑定变更时，所有依赖它们的验证、审核和声明自动标记 `INVALIDATED`，不得继承。
11. **状态合法性**：Schema、枚举、阶段跃迁和角色字段合法；状态文件不得自定义 required case、覆盖范围或 PASS 结论。
12. **检查点闭合**：每份 execution evidence 声明其 `checkpointIds[]`，控制器从原始 case evidence 重算自动 checkpoint 结果。Review 对每个自动 checkpoint 恰好记录一次；owner decision 对每个适用 HUMAN checkpoint 恰好决定一次。总计数不能替代逐 checkpoint provenance。
13. **发行路径闭合**：本地实验禁止 release；私有运行要求候选绑定的独立审核和 owner 决定；外部 R3 正式发行的 receipt 精确引用当前 candidate、decision 和受管 external-release-audit，审计报告精确引用当前 candidate、review、evidence、transcript 与控制器 manifest。任一上游引用或必需签名变化均使对应资格无效。
14. **执行隔离**：本地 runtime-observed case 必须在 candidate commit 的干净 detached Git worktree 中执行；调用方工作树的未跟踪、ignored、缓存或临时 runner 不得成为候选执行输入。

硬检查不得因紧急、历史结论、负责人判断、用户口头同意或 warning 接受而豁免。可选动作只有：修复事实、重跑证据、冻结新候选、降低声明，或保持未通过。

## 确定性结果

| 结果 | 含义 |
|---|---|
| `PASS` | 当前范围内的结构、绑定、守恒和失效检查合格；不等于产品或发布质量已获批准。 |
| `BLOCKED` | 缺少环境、输入、权限、人工授权或前置条件，尚不能执行或收口。 |
| `FAIL` | 已观察到确定性矛盾、缺失或硬规则违反。 |
| `INVALIDATED` | 上游绑定变化使既有验证/审核/声明不再适用，必须重新生成。 |
| `NOT_APPLICABLE` | 当前合同和阶段明确不适用；必须有可追溯理由，不能用于跳过 required case。 |

硬检查**只阻断声明跃迁**，例如从 `DEVELOPMENT_CHECKED` 声称为 `VERIFIED` 或 `ACCEPTED`。它们不阻止诊断、修复、调查或普通开发；也不自动宣布产品“通过”。

## 外部可观察证据

可支持 PASS 的观测来源仅包括：

- `runtime-observed`：真实运行时产生的日志、trace、请求/响应、二进制或实际状态。
- `blackbox-observed`：从用户/系统可见边界采集的截图、录屏、交互结果、设备记录或外部接口响应。

`derived`（聚合、计算、图像度量）与 `declared`（文档、配置、模型/程序自报）可以辅助解释，但不得单独支持 PASS。文件存在、内部数组、DOM 标签、理论值、固定 diagnostics 和未验证的报告均属于不足证据。

## Adapter、MCP 与 Skill 的证明边界

- `generic-command` 只证明精确锁定命令、退出码、原始 transcript、逐 case counters 和明确产物；它不自动证明真实 UI、部署、性能或安全充分性。
- `browser-runtime` 可以证明真实浏览器/Playwright 路径及截图、trace、日志和 counters；它不能证明 Tauri/Electron/Capacitor 原生壳、安装包、目标设备、GPU/能耗或主观视觉质量。
- `browser-webgl-game-runtime` 只在 `GAMEPLAY` 与显式 WebGL target 同时成立时，以直接锁定的 `playwright test`、合同锁定时已验证的安全产物路径、执行前清理后新生成的候选绑定产物证明浏览器玩法切片；`--help`、`--version`、`--list`、UI 模式和允许零测试等非执行入口不合格。控制器只记录宿主平台和实际 Playwright 版本，浏览器、视口和 WebGL 细节仅在锁定产物实际记录时才进入证明范围。它不能证明原生壳、Android 真机、目标硬件、GPU/热稳定、游戏感、人工批准或发行资格。
- `godot-runtime` 必须绑定 `project.godot`、Godot 可执行文件和精确版本；headless smoke 不能证明渲染玩法、目标硬件表现或游戏感。
- Tauri、Electron、Unreal 与 Capacitor 在 0.3.6 没有正式 adapter；发现它们只能产生 investigation。已闭合的 Browser/generic 证据只在其原有范围内有效。
- MCP 不由 Python runtime 直连。其结果只能作为 external evidence 导入，且必须绑定 adapter、工具版本、操作、原始 transcript、产物、candidate、case、positioning 与 rule-set；命令返回文本或工具自报成功不能覆盖 required case。
- Required Skill 可生产证据，advisory Skill 可给启发式意见，但所有 Skill 都必须 `canApprove=false`。无法内容寻址的 Skill 只能 advisory；安装 Skill 需要用户批准但不需要私钥。

定位与规则相关硬结果统一使用：`HC-POSITIONING-SCHEMA`、`HC-POSITIONING-CONFIRMED`、`HC-RULESET-BINDING`、`HC-RULESET-CONFLICT`、`HC-RULESET-NON-WEAKENING`、`HC-RULE-CASE-COVERAGE`、`HC-ADAPTER-CAPABILITY`、`HC-SKILL-BINDING`、`VC-SKILL-INSTALL-APPROVAL`。Schema 2.0 控制面固定返回 `VC-REINSTALL-REQUIRED`，不能靠旧证据通过 3.0 门禁。

## 非硬发现的边界

- `warning`：已有证据提示风险，但不存在可机械裁决的违反；必须注明事实、影响范围、建议行动和触发它的事件。
- `investigation`：缺少关键事实，必须明确待验证命题、所需证据、负责人和停止条件；不得暗示已经失败。
- `human-decision`：目标、范围、主观体验、风险接受、授权、不可逆动作或无法自动化的质量判断；必须说明可选项与后果。

warning 不得升级为 FAIL，除非后续证据命中硬检查。接受 warning 也不等于放行：记录必须绑定任务、候选、理由、适用范围与**失效事件**（如候选变化、合同变化、到期日期或环境变化）。失效后 warning 的接受状态自动作废并重新评估。

结构化审核使用八类发现：`CURRENT_GOAL_DEFECT`、`MINIMUM_CORE_VIOLATION`、`SAFETY_OVERRIDE`、`HUMAN_DECISION`、`PROCESS_WARNING`、`INVESTIGATION`、`FUTURE_PROPOSAL`、`OUT_OF_SCOPE`。只有前三类在具备目标引用、受影响声明和非空证据时可直接阻断；`SAFETY_OVERRIDE` 必须引用当前 `KF-*`；`HUMAN_DECISION` 只阻断列明的声明；其余分类保持非硬。P0–P3 严重度用于描述影响，不能自行把 warning、未来建议或已排除威胁模型升级为当前 blocker。
