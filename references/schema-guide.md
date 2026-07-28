# 机器接口指南（Schema 3.2）

Schema 位于 `assets/project-control/schemas/`，模板位于 `assets/project-control/templates/`。Bootstrap 后，适用 Schema、规则目录与校验器固定复制到 `.vibe-control/runtime/0.3.4/`。所有 0.3.4 项目机器对象使用 `schemaVersion="3.2"`；不得把旧对象改字段后冒充 3.2。

## 对象与所有权

主要闭包为：

`package-audit-receipt/package-development-binding → key-objectives-lock + project-positioning + resolved-rule-set → project-governance-lock → task-contract(checkpoints + confirmation) → task-lock → candidate-manifest → execution-evidence/external-evidence-attestation → review-attestation(checkpoint results)/audit-closure → approval-signature(checkpoint decisions) → external-release-audit/release-receipt → stage-state/handoff`

连接必须使用稳定 ID、项目根相对路径和 SHA-256，不能凭文件名、聊天描述或状态自报猜测。

- `key-objectives-lock.json` 绑定根级 `KEY_OBJECTIVES.md`、需求来源、修订、确认记录、ID 集合与 SHA-256；机器不判断目标文本质量。
- `project-positioning.json` 保存用户确认的项目定位轴、确认记录与规范化摘要哈希；发现事实不能自证用户确认。
- `resolved-rule-set.json` 只能由控制器从六层输入确定性编译：`CORE → EXPERIENCE → CAPABILITY_PROFILE → RUNTIME_ADAPTER → SKILL_BINDING → PROJECT_OVERLAY`。它是 Profile、adapter、Skill routing 和 overlay 的唯一机器结果，不得另建第二份规则状态。
- `project-governance-lock.json` 内容绑定关键目标、positioning、resolved rule set、case catalog、权威文件、规则编译器/目录、Skill package、固定 runtime 和 package mode。`DEVELOPMENT` 绑定干净精确开发候选并限制声明；`SEALED` 绑定包级审计收据。
- `task-lock` 从当前规则集派生 `applicableRuleIds[]` 和 `requiredCaseCapabilities[]`；任务合同必须用 `objectiveRefs[]` 引用当前 `KO/KF`，包含已确认 checkpoint set，且不能删除或降低派生要求。
- `case-catalog` 的 required case 通过 `satisfiesRuleIds[]` 显式覆盖适用规则。Oracle 固定 `exitCode`、`stdoutContainsAll[]`、`stderrContainsNone[]`，artifact 固定安全相对路径与 `minBytes`。总 case 数或一项万能执行不能替代逐规则/逐 case 覆盖。
- `candidate-manifest` 直接绑定关键目标、需求来源、positioning、resolved rule set、task lock、`checkpointSetSha256`、commit/tree 和全部输入；evidence、review、decision 与 handoff 必须携带同一 checkpoint hash。
- `review-attestation.checkpointResults[]` 对每个自动 checkpoint 恰好一项，observed status 由控制器从原始 evidence 重算。`approval-signature.checkpointDecisions[]` 对适用 HUMAN checkpoint 恰好一项，只接受显式 `PASS`。
- `audit-closure` 在探索预算超限时绑定候选、checkpoint hash、失败 review hash 和 finding IDs，阻止同一候选跨会话重置预算。
- `stage-state.json` 由控制器重新派生并原子写入。手改 phase、health 或 claimLevel 不会产生资格。

## 定位、发行意图与声明上限

`releaseIntent` 枚举为 `LOCAL_EXPERIMENT | PRIVATE_OPERATION | EXTERNAL_RELEASE`，既存在于已确认 positioning，也由治理锁重复绑定以便机械核对；两者不一致必须失败。它与 `deliveryObjective`、任务 `risk` 分轴。

声明上限取 phase、任务合同、全部 required cases、规则集和 release intent 各自上限的最小值。任务只能缩小定位范围，不能改写定位或削弱规则。任何 positioning、Profile、adapter、Skill binding、overlay 或规则目录变化都会失效 task lock 及全部下游对象。

`review-attestation` 与 `approval-signature` 的 `keyId/signature` 在结构上可选：本地实验和私有运行使用受版本管理、候选绑定的人工记录；只有 `EXTERNAL_RELEASE + R3 + RELEASE_READY` 项目路径要求 Ed25519 验签。`external-release-audit` 与 `release-receipt` 只属于这条项目发行路径。

`package-audit-report`、`package-audit-evidence-manifest` 与 `package-audit-receipt` 属于 Skill 包级最终候选审计闭包，不是项目 release receipt。annotated audit tag 指向一个 Git tree，其中固定包含报告、执行证据 manifest、逐 case 原始 transcript 与声明 artifact；annotated release tag 的 JSON message 再绑定该 tree、报告/evidence blob、精确候选与三项内容哈希。它们不使用私钥，也不构成安装、授权或收费机制。Bootstrap 只在包级 validator 重新验证当前 package/runtime inventory、全部证据 blob 与固定控制覆盖后，把规范化收据复制到项目治理目录并由治理锁内容绑定。

## Adapter 与 Skill binding

Adapter descriptor 绑定 ID、版本、内容哈希、runtime family、发现来源、执行模式、机器可接受的 `provesCaseCapabilities[]`、明确非证明事项及环境限制。Case 自报能力必须是该集合的子集；超界声明不能形成规则覆盖。0.3.4 仅实现 `generic-command`、`browser-runtime`、`godot-runtime`；Tauri、Electron、Unreal 与 Capacitor 只能产生 investigation。

Skill binding 固定 Skill ID、`required | advisory`、`producer | heuristic-reviewer`、触发条件、写权限、`canApprove=false`、路径、版本和确定性 tree hash。required 缺失或漂移阻断任务；advisory 缺失只告警；无法内容寻址的 Skill 只能 advisory。安装需要单独人工批准，完成后必须重新发现和解析。任何 Skill 安装均不需要私钥。

## 文件引用

正式引用统一包含：

```json
{"path":"project/root/relative/path","bytes":123,"sha256":"64-lowercase-hex","tracked":true}
```

禁止绝对路径、反斜杠、`..`、符号链接逃逸和项目外文件。正式控制对象、transcript 与产物必须受 Git 跟踪；无 Git 时只允许诊断。

## CLI envelope 与状态三轴

CLI 统一输出 Schema 3.2 JSON envelope，`status` 只能是 `PASS | BLOCKED | FAIL | INVALIDATED`，并分离 `integrity`、`formal`、`state`、warning、investigation 和 human decision。退出码固定为 `0/2/3/4`；错误参数、畸形 JSON、Schema 失败和内部异常也必须输出稳定 JSON，不能泄漏 argparse 文本或 traceback。

- `phase`：生命周期位置；
- `health`：`CLEAR | BLOCKED | FAILED`；
- `claimLevel`：当前完整证据能支持的最高声明。

不得创建 `PARTIAL_PASS`、`ALMOST_DONE` 等替代状态。失败和阻断不伪装成阶段。

## 兼容、迁移与重装

Schema 3.1 使用 `migrate --plan [--spec]` 与 `--apply <plan-hash> --spec`。无 spec 的计划只读生成内容 ID 和待补映射；确认 spec 后，apply 在 staging 中验证完整 3.2 控制面和逐文件 archive manifest，再原子替换。旧 task、candidate、evidence、review、decision、receipt 和 handoff 只归档、不重绑定，状态回到 `DRAFT / BLOCKED / DIAGNOSTIC`。

0.3.4 不迁移 Schema 2.0 数据。检测到 Schema 2.0 控制面时返回 `VC-REINSTALL-REQUIRED`，不得写入；该项目可继续使用固定 0.2.2 runtime，或经批准后全新 bootstrap。

Schema 3.2 项目改变里程碑、目标环境或发行边界时使用 `reposition --plan` 计算精确变化和失效集合；只有批准并匹配 plan hash 后才能 apply，随后状态回到 `DRAFT/DIAGNOSTIC`。

关键目标变化使用 `revise-objectives --plan/--apply <plan-hash>`。计划必须列出目标/需求来源差异和 task、candidate、evidence、review、decision、handoff 失效集合；普通任务、worker 或审核者不得直接改写目标锁。

JSON Schema Draft 2020-12 由固定依赖执行。Git、哈希、跨文件闭包、规则编译、计数、candidate、签名和失效关系由固定 runtime 机械重算；依赖缺失或版本不符时不自动安装并返回 `DEPENDENCY_BLOCKED`。
