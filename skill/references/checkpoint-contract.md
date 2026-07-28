# 检查点契约：预设通过条件与审计停止边界

本规范位于“项目定位已确认”之后、“任务规划”之前。它把 success signal、固定 case、oracle 和人工质量门转换为候选绑定的可观察通过条件，防止审核者在缺少预期结果时无限扩大审计面。

## 一次性建立

1. 规范化每条 success signal 与 human quality gate：Unicode NFC、去除首尾空白、连续空白折叠；保留大小写和标点。
2. ID 分别为 `SIG-<sha256前12位>` 与 `HG-<sha256前12位>`。重复规范化文本、同 ID 异文或非内容派生 ID 必须失败。
3. 为每条 success signal 建立且只建立一个 checkpoint。任务最高可到 `ACCEPTED` 时，每个 human gate 也必须恰好映射一个 `HUMAN` checkpoint。
4. 向用户一次展示：检查点事实、case/assertion、预期结果、声明等级以及 `notProven`。用户只确认完整摘要，不逐条参与技术核对。
5. 将 `acceptanceCheckpoints + auditPolicy` 的规范 JSON 做 SHA-256，写入 confirmation、task lock 和所有候选后对象。

控制器只判断结构、身份、哈希和观察结果，不判断检查点文字是否有产品价值。后者来自已确认需求和用户决策。

## 检查点闭包

- `AUTOMATED` 必须至少有一个当前任务 case 和一个 assertion；assertion 的 case 必须是其 checkpoint case 的非空子集。
- `HUMAN` 必须引用一个锁定的 `HG-*`。它可以查看运行证据，但测试不能替 owner 自动批准。
- 每个 required case 至少被一个 checkpoint 使用；一个 case 可以支持多个 checkpoint。
- checkpoint 的 objective 必须属于当前 task；声明等级不得超过 task、release intent 或其 case 的最小 ceiling。
- case oracle 至少锁定 `exitCode`、`stdoutContainsAll[]`、`stderrContainsNone[]`，artifact 以 `{path,minBytes}` 描述。退出码为零但文字或产物不符仍为 FAIL。
- checkpoint、assertion、case、oracle、confirmation 或其哈希变化，使 candidate、evidence、review、decision 与 handoff 失效。

## 审核与停止

固定策略只有一套：

```text
mode = CONFORMANCE_PLUS_BOUNDED_EXPLORATION
maxExploratoryFindings = 3
stopCondition = ALL_REQUIRED_CHECKPOINTS_REPORTED
```

审核者获得预设 checkpoint 和证据接口，但不得获得候选实际结果、实现者结论、已知缺陷清单或预期总裁决。控制器从原始 evidence 重算每个自动 checkpoint 的 observed status；遗漏、重复、自报不一致、偏差无 finding 或总裁决冲突均失败。

全部自动 checkpoint 已报告后，合规审核必须停止。每个候选最多保留三项 `PROCESS_WARNING / INVESTIGATION / FUTURE_PROPOSAL / OUT_OF_SCOPE` 探索发现；第四项被拒绝。没有新候选、合同变化或用户授权，不得重复发起开放式审核。

若候选绑定 review 提交第四项普通探索发现，controller 同时写入 `audit-closure`，以 `EXPLORATION_BUDGET_EXHAUSTED` 关闭该候选的开放式审核。失败 review 不能通过更换 finding ID 重新获得三项预算；后续尝试命中 `HC-AUDIT-STOP-CLOSURE`。继续调查必须形成新候选或新任务边界；用户若明确授权重开，也必须通过该新边界落盘，不能删除或改写关闭记录来重置预算。

真实的 `CURRENT_GOAL_DEFECT`、`MINIMUM_CORE_VIOLATION` 与 `SAFETY_OVERRIDE` 不占探索预算，但必须分别满足：

- 当前目标缺陷引用当前 task checkpoint 和该 checkpoint 的 task objective；
- 最低核心违规引用固定 core control 和非空证据；
- 安全越界引用当前锁定的 `KF-*`；
- `affectedClaims[]` 形成向上闭包，且只阻断明确列出的声明。只影响 `RELEASE_READY` 的 finding 不阻断 `VERIFIED`。

## 人工决定

进入 `ACCEPTED` 时，owner decision 必须一次列全适用的 HUMAN checkpoint。缺项、重复、未知项、候选/检查点哈希漂移或任一 `REJECT` 均不得进入 `ACCEPTED`。人工批准不外推到未列 checkpoint 或更高声明。

## 3.1 → 3.2 迁移

- 无 spec 的 `migrate --plan` 只读列出确定性新 ID、case/oracle 转换、待补 checkpoint 和失效集合。
- 主代理根据需求草拟完整映射，用户一次确认；带 spec 的 plan 绑定旧控制面快照、spec 和确认摘要。
- apply 要求干净工作树、受 Git 跟踪的 spec 和精确 plan hash。它先在临时目录构造、验证完整 3.2 控制面，再原子替换。
- 旧控制面完整复制到 `.vibe-control/legacy/schema-3.1/<plan-hash>/control-plane/`，并生成逐文件 bytes/SHA-256 manifest。
- 旧 task、candidate、evidence、review、decision、receipt 和 handoff 仅是历史诊断，不能自动重绑定；新状态回到 `DRAFT / BLOCKED / DIAGNOSTIC`。
- Schema 2.0 不属于该迁移路径，必须返回 `VC-REINSTALL-REQUIRED`。
