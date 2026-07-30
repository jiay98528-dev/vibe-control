# 机器接口指南（Schema 4.0）

Schema 位于 `assets/project-control/schemas/`，模板位于 `assets/project-control/templates/`。Bootstrap 后，适用 Schema、规则目录和校验器固定复制到 `.vibe-control/runtime/0.4.0/`。不得通过只改 `schemaVersion` 冒充 4.0。

## 权威闭包

主要链路为：

```text
package binding
→ key-objectives-lock + project-positioning + automation-policy + resolved-rule-set
→ project-governance-lock
→ task-contract(action map + checkpoints + scorecard + verification/guard/reporting policies)
→ task-lock → candidate-manifest
→ execution-evidence → review-attestation → owner decision
→ stage-state / release objects / handoff
```

对象以稳定 ID、项目根相对路径、字节数和 SHA-256 连接。状态文件不能同时定义 required case、证据覆盖和最终 PASS。

## Schema 4.0 新对象和字段

`task-contract` 除 3.2 检查点字段外，必须包含：

- `milestones[]`：稳定 ID、外部可见结果、依赖、节点、checkpoint refs 和预期通过条件；
- `scorecardPlan[]`：计分项 ID、四域之一、事实来源和权重；每个领域至少一项；
- `verificationStrategy`：Implementer 最小开发检查、候选后 case、Executor/Auditor 分工、未证明边界和审核停止条件；
- `guardPolicy`：`ACTION_GUARD | CLAIM_GUARD | HUMAN_DECISION | ENVIRONMENT_BLOCKED | ADVISORY` 的项目适用规则；
- `reportingPolicy`：固定 `ZERO_CONTEXT_ORIENTATION`、普通话字段、下一步三入口和进度更新策略。

控制器为行动地图、计分计划和三项策略分别生成规范化哈希，并合成为 task lock 的 task-plan identity。Candidate、evidence、review、decision 和 handoff 必须携带相同身份；任何里程碑、计分项、检查点、case/oracle、策略或确认记录变化都会失效全部下游对象。

`automation-policy` 的 4.0 默认值为：

```text
AUTO_LOCAL_TO_REVIEW / MILESTONE_COMMITS / NONE
```

默认值无需用户确认，但必须绑定项目、目标和定位身份。手动模式或 push 模式仍通过内容绑定 plan/apply 改变；push 记录精确 remote、branch、upstream 和去凭据 URL 哈希。

## 本机 progress ledger

`progress-ledger.json`、`status.json`、`summary.md` 和 `index.html` 位于用户缓存，不是项目 Schema 对象，不进入 governance lock、candidate、evidence 或 package manifest。它们可以在 `.vibe-control` 出现前存在。

Ledger 使用 `projectInstanceId`、`taskId`、单调 `revision`、节点状态与 append-only event。Coordinator 是唯一写入者；Team/SubAgent 回报不能直接成为 ledger 事件。写入时比较 `expectedRevision` 并原子替换，冲突不得覆盖。Ledger 丢失后不得从 narrative 重建历史。

Dashboard 从 ledger、机器对象、Git 实况和共享只读 validation projection 派生。它不能写 stage-state/evidence、关闭 blocker 或提高 claim。详细接口见 [progress-dashboard.md](progress-dashboard.md)。

## 四域计分

域枚举为：

```text
FUNCTIONALITY | ROBUSTNESS_SECURITY | AUDIT | PROCESS
```

每项状态从引用事实派生；未知、待处理、无候选绑定证据或自报结果不计入完成。Dashboard 同时输出完成项、总项、比率和证据覆盖。综合准备度固定为 `40/25/20/15` 加权并保留一位小数；没有锁定分母时输出 `N/A`。计分不覆盖硬 blocker 或声明上限。

## 报告 envelope

CLI 保持 `status = PASS | BLOCKED | FAIL | INVALIDATED` 及 `integrity/formal/state/data/error` 分离，并在执行、门禁和审计报告中增加：

```json
{
  "plainLanguage": {
    "projectPurpose": "...",
    "whatWasDone": "...",
    "whatWorksNow": "...",
    "whatStillDoesNotWork": "...",
    "userImpact": "...",
    "canContinue": "...",
    "canRelease": "..."
  }
}
```

文本报告中该对象必须投影到最后一节“给没有开发背景的人看的说明”。字段不得包含内部 ID、哈希、Schema、claim、commit/tree 等控制面术语。机器检查字段齐全、位置和已知术语泄漏，不假装判断文字质量。

停止报告还包含三个建议：`RECOMMENDED`、`ALTERNATIVE`、`OPEN`。建议是 UI/模型路由输入，不是事实、授权或状态跃迁。

## 既有闭包保持不变

- `key-objectives-lock` 绑定受跟踪目标文档、来源、确认、修订、ID 集合和哈希；机器不评价目标文字。
- Positioning 分离 `deliveryObjective`、`releaseIntent`、runtime、环境与渠道；任务不能改写项目定位。
- `resolved-rule-set` 由规则层确定性编译，任务只能增加或缩小约束。
- Required case 必须为 `CANDIDATE_EXECUTION` 并逐规则覆盖；`BOOTSTRAP_DIAGNOSTIC` 不能进入候选任务。
- Candidate 绑定 commit/tree、task plan、case/oracle、目标、定位、规则和全部输入。
- 每个 required case 有候选绑定的真实 execution、非零守恒 counters、零 skip、transcript/artifact；总计数不能替代逐 case provenance。
- Review 逐检查点闭合，Owner 逐项决定 HUMAN checkpoint；实现者、执行者和审核者不能自批。
- Stage state 由控制器重新派生并原子写入，手改不产生资格。

## 发行意图和证据

`LOCAL_EXPERIMENT | PRIVATE_OPERATION | EXTERNAL_RELEASE` 继续分离。声明上限取 phase、task、case、rule set、release intent 和 package posture 的最小值。R2/私有 R3 的 review/decision 是候选绑定人工记录；仅外部 R3 `RELEASE_READY` 使用项目级签名链。Skill 安装和本地版本不需要私钥。

正式文件引用继续禁止绝对路径、反斜杠、`..`、符号链接逃逸、未跟踪文件和哈希漂移。CLI 退出码保持 `0/2/3/4`，坏参数、畸形 JSON、Schema 错误和内部异常必须输出稳定 JSON。

## 迁移与兼容

首次用 0.4.0 `adopt/resume` 处理 Schema 3.2 时，生成内容绑定升级计划，完整归档旧控制对象和逐文件 manifest，不继承 task、candidate、evidence、review、decision、receipt 或 handoff。Apply 在 staging 完整验证后原子替换，失败回滚；成功后使用默认本地自动策略并回到 `DRAFT / BLOCKED / DIAGNOSTIC`。脏工作树或不可无损解析时只输出计划/阻断，不修改产品。

Schema 3.1 仍先按既有路径迁移；Schema 2.0 返回 `VC-REINSTALL-REQUIRED`。关键目标变化使用 `revise-objectives`，定位变化使用 `reposition`，自动化副作用权限变化使用 `automation`；均不得藏在普通 task 中。
