# 领域 Profile：Schema 3.2 的增量规则层

Profile 是版本化机器规则目录，不是技术栈模板、设计规范或产品需求。它由 [project-positioning.md](project-positioning.md) 中已确认的 `primaryExperience` 与 `capabilityDomains[]` 激活，只能在通用规则上增加 case、证据、warning、investigation 或人工决策点。

## 组合规则

- `GAMEPLAY` 或 `REALTIME_ENGINE` 激活 `game`；
- `USER_INTERFACE` 激活 `ui-desktop`；
- `BACKEND_API` 激活 `backend-api`；
- `DATA_PIPELINE` 或 `LLM` 激活 `data-llm`。

多个适用 Profile 采用 AND：全部规则和 required case 能力都进入同一个 `resolved-rule-set.json`。不能用“主 Profile”覆盖次要能力，也不能用某个 Profile 的 PASS 代偿另一 Profile 未覆盖的规则。相同 rule ID 且规范化内容一致时可确定性去重；内容冲突必须以 `HC-RULESET-CONFLICT` fail-closed。

Profile 不得覆盖项目目标、事实源优先级、架构、合法性判断、体验标准、风险或发行意图。主观评价只能生成 `human-decision`；事实源冲突只能生成 `investigation`。任务合同只能缩小任务范围，不能删除已派生的 `applicableRuleIds` 或 `requiredCaseCapabilities`。

## `ui-desktop`

适用于桌面、Web、移动端、仪表盘及交互式可视化。

- 增加真实运行时、目标视口、输入路径（键盘、触控、焦点）和故障恢复的外部可观察 case。
- 若存在视觉事实源，绑定其版本、视口和批准范围；批准静态图不外推为交互、性能、可访问性或发布批准。
- 要求记录可复现截图、录屏、trace 或实际运行日志；内部 DOM 标签、CSS 声明和自报 diagnostics 不能单独支持 PASS。
- 可读性、视觉匹配、动效自然度和产品成熟度留给候选绑定的人工审阅；自动图像差异只能是辅助证据或 warning。

`browser-runtime` 可以证明真实浏览器/Playwright 路径及其产物，但不能证明 Tauri/Electron/Capacitor 原生壳、安装包、目标硬件或主观质量。这些原生 runtime 在 0.3.6 只产生 investigation。

## `game`

适用于游戏、互动叙事、实时引擎和玩法原型。

- 以玩法、剧情、关卡、输入、性能预算、资产许可和目标平台为项目事实源；禁止用通用网页审查代替游戏 UX 审查。
- 增加真实引擎运行、输入回路、状态保存/恢复、失败状态和目标硬件或性能档位的 case。
- 资产链必须能追溯来源、许可、版本和运行时绑定；占位资产、编辑器预览或网页壳截图不能外推为正式游戏体验。
- 乐趣、节奏、叙事、镜头感和美术质量为 `human-decision`；Profile 只保证它们被提出、记录并绑定候选。

`godot-runtime` 绑定 `project.godot`、Godot 可执行文件、精确版本和候选 detached worktree 的原始输出。Headless smoke 只证明该执行路径，不证明渲染玩法、目标设备性能或游戏感。Unreal 在 0.3.6 只发现并产生 investigation。浏览器游戏可同时使用 `game` 与 `ui-desktop`，两者仍按 AND 闭合。

## `backend-api`

适用于服务端、API、队列、数据库与集成服务。

- 增加接口 schema、认证授权、幂等、错误语义、兼容性、迁移、回滚和故障注入的可观察 case。
- 对数据写入、迁移、权限扩大和外部副作用按风险升级；不可逆操作必须有用户授权与恢复证据。
- 运行日志、请求/响应、迁移结果和可复现测试产物优先于 mock、自报状态或仅代码审阅。
- 吞吐、延迟、容量和安全充分性若没有真实 workload/环境证据，只能是 `investigation` 或受限声明。

`generic-command` 只能证明被锁定命令的退出码、transcript、counters、明确产物，以及 descriptor 明列的 case 能力；不能用 case 自报标签扩张证明边界，也不能把单元测试或启动日志外推为真实 UI、游戏运行、部署、容量、安全或外部集成通过。

## `data-llm`

适用于数据管道、模型调用、RAG、评测、Prompt 和自动化决策。

- 绑定数据集/切分、Prompt、模型与参数版本，并增加泄漏、权限、成本、非确定性和失败回退的 case。
- 要求记录评测输入、执行环境、输出样本、聚合方式和成本/配额来源；单次漂亮输出、人工挑样或模型自评不能单独 PASS。
- 涉及个人数据、敏感数据、对外行动或高影响决策时，增加人工授权和安全审阅点。
- 输出质量、事实性、偏见、创意与业务可用性必须保留为人工评审或明确标注的启发式结论。

## Adapter 与 Skill 不是 Profile 替代品

Profile 说明“必须证明什么”；adapter descriptor 说明“某执行器确实能证明什么”；Skill binding 说明“哪个 Skill 可以生产证据或给出启发式意见”。三者必须同时进入唯一规则集，不能互相外推。

- `generic-command`、`browser-runtime`、`godot-runtime` 是 0.3.6 的已实现 adapter 类别；
- Tauri、Electron、Unreal 与 Capacitor 只允许 discovery/investigation；
- MCP 输出作为带原始 transcript 的外部证据导入，不能由 Python runtime 直连后自报 PASS；
- required Skill 必须有路径、版本和确定性 tree hash，缺失或漂移阻断相应任务；
- advisory Skill 缺失只产生 warning，无法内容寻址的 Skill 只能 advisory；
- Skill 可作为 `producer` 或 `heuristic-reviewer`，但必须 `canApprove=false`。

## 项目本地 overlay

内置 Profile 不足时可使用项目本地机器可读 overlay。overlay 只能 `ADD` 领域规则、case 能力、证据要求、warning、investigation 或人工决定；不得删除规则、降低观察来源、放宽 claim ceiling、重定义阶段/PASS 或绕过通用硬检查。

overlay 必须说明适用任务、事实源、外部可观察 case 和失效条件。内容冲突或弱化尝试必须由 `HC-RULESET-NON-WEAKENING` fail-closed；不能机械判定的新增要求保持 warning、investigation 或 human-decision。
