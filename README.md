<div align="center">

# vibe-control

**把“人工盯住 Agent”变成“项目启动时建立可观察、可验证的控制面”。**

Observable control planes for VibeCoding and multi-agent software development.

![Version](https://img.shields.io/badge/version-0.4.0-2563eb)
![Maturity](https://img.shields.io/badge/maturity-DEVELOPMENT__DIAGNOSTIC-f59e0b)
![Schema](https://img.shields.io/badge/schema-4.0-7c3aed)
![Python](https://img.shields.io/badge/python-3.12-3776ab)

</div>

> [!WARNING]
> 当前公开包是 `0.4.0 DEVELOPMENT_DIAGNOSTIC`。仓库公开可用不等于已经完成正式封印；`formalClaimsAllowed=false`，不得外推为产品验收或发布通过。

## 它解决什么问题

VibeCoding 的典型失败往往不是“模型完全不会写代码”，而是开发过程中逐渐出现：目标漂移、局部测试冒充整体通过、实现与验收标准共同变化、跨会话证据丢失，以及审核范围无限外扩。

`vibe-control` 是面向产品负责人、主 Agent、实现 Worker 与独立 Auditor 的治理 Skill。它采用“强边界、弱流程”：只把可确定验证的事实做成硬检查，架构和实现策略仍由模型在已确认边界内自主决定。

## 核心能力

| 能力 | 作用 |
| --- | --- |
| 关键目标缰绳 | 从需求事实源推导并锁定 `KEY_OBJECTIVES.md`，阻止审计和修复逐轮跑偏。 |
| 启动即有操作台 | 在项目写入前建立本机离线网页，持续显示项目用途、当前任务、四维准备度、已完成事项和下一步。 |
| 零背景说明 | 每份执行、门禁和审核报告最后都用没有开发术语的文字说明“现在能做什么、还不能做什么、会有什么影响”。 |
| 检查点契约 | 在实现前固定“什么结果算通过”，绑定 case、oracle、assertion 与声明上限。 |
| 候选与证据闭包 | 绑定精确 commit/tree、输入哈希、逐 case provenance、transcript、产物与 counters。 |
| Windows 候选执行 | 保留锁定逻辑命令，显式解析 `pnpm.cmd` 等宿主 executable；参数不经过 shell。 |
| Evidence 字节身份 | 嵌套 Git attributes 禁止换行/filter 转换，并复核工作副本与提交 blob。 |
| 同 Schema 升级 | 内容绑定地归档旧控制链、原子安装新 runtime，并使旧 PASS 全部失效。 |
| 有界审核 | 先审核预设检查点；普通探索发现有候选级预算，达到停止条件后结束审核。 |
| 多智能体边界 | 单一控制面写入者、隔离写入、无答案泄漏审核；Worker 回报不能自行批准。 |
| 跨宿主兼容 | 按实际能力选择 `TEAM → SUBAGENT → SERIAL`，不伪造宿主不存在的功能。 |
| 默认本地自动推进 | 自动规划、委派、实现、定向检查、冻结候选和独立核对；不在普通阶段反复询问，只在真正需要用户决定时停止。 |
| 执行侧瘦身 | 实现者只拿轻量任务卡并运行与改动直接相关的检查；完整验收与审核由独立角色在候选冻结后执行。 |
| 有界 Git 副作用 | 自动 commit/push 绑定任务、完整提交历史、既有 upstream 与非强制 fast-forward；越界历史不能借“工作树干净”绕过。 |
| 离线复核仪表盘 | 在人工复核点生成同源 `index.html`、`status.json` 和 `summary.md`，明确区分已证明与未证明。 |
| 发行意图分级 | 区分本地实验、私有运行和外部发行，避免把同一套高强度门禁施加给所有项目。 |
| 便携安装身份 | 区分 Git 根、Git 子目录和无 Git 副本；普通下载不再被正式封印所需的 Git 前置误伤。 |
| 有界深度测试 | 长回归按叶级 case 并发执行，提供独立超时、实时进度和守恒的最终计数。 |

```mermaid
flowchart LR
    A["启动操作台"] --> B["需求与关键目标"]
    B --> C["行动地图与预期结果"]
    C --> D["Team / SubAgent 实现"]
    D --> E["候选冻结与独立验收"]
    E --> F["独立审核"]
    F --> G["操作台复核 / 人工决定"]
```

## 快速开始

### 1. 安装到 Codex

在 Codex 中使用内置 Skill Installer（推荐）：

```text
使用 $skill-installer 安装 https://github.com/jiay98528-dev/vibe-control/tree/main/skill
```

也可以手工安装（PowerShell）：

```powershell
git clone https://github.com/jiay98528-dev/vibe-control.git vibe-control-repo
Copy-Item -Recurse .\vibe-control-repo\skill "$env:USERPROFILE\.codex\skills\vibe-control"
```

完成后运行普通安装自检：

```powershell
python "$env:USERPROFILE\.codex\skills\vibe-control\scripts\validate_installation.py" `
  --skill-root "$env:USERPROFILE\.codex\skills\vibe-control"
```

标准下载应报告 `status=PASS`、`sourceKind=PORTABLE_COPY`、`packageMode=DEVELOPMENT` 和 `maxClaimLevel=DEVELOPMENT_CHECKED`。这证明安装内容可用于诊断开发，不证明 Git 来源或正式封印。`validate_package_release.py` 与两套深度对抗回归属于维护者封印流程，不是普通安装步骤。

### 2. 启动项目

在新项目或需要接入治理的现有项目中调用：

```text
使用 $vibe-control 启动这个项目。先建立关键目标、发行意图和验收检查点，再进入实现。
```

Skill 会先建立本机操作台，再确认需求、关键目标、项目定位和预期发行状态。随后默认采用 `AUTO_LOCAL_TO_REVIEW / MILESTONE_COMMITS / NONE`：自动规划、委派、实现、定向检查、冻结候选和独立核对，但不推送、不创建远端／PR／tag／release，也不自动批准主观检查点。

普通阶段不会逐次等待确认。它只会在候选已准备好、需要主观体验判断、目标／范围／权限要改变、不可逆或高风险操作、环境无法继续、重复失败没有变化、远端冲突或用户中断时停止，并同时给出离线操作台、文字摘要以及两个具体建议和一个自由输入入口。用户仍可明确要求手动模式；推送必须另有精确的上游授权。

### 3. 非 Codex Agent

将 [`skill/`](skill/) 目录安装到宿主的 Skill 目录。运行时按实际工具能力降级：

1. 有持久任务、独立上下文、消息、等待和工作隔离能力：`TEAM`；
2. 没有完整 Team、但有子智能体 spawn/message/wait：`SUBAGENT`；
3. 两者都没有：`SERIAL`。

Skill 不设置固定子智能体数量上限，但仍服从宿主容量、任务可分解性、文件所有权与工作树隔离边界。

## 当前成熟度

- 版本：`0.4.0`
- Schema：`4.0`
- 首个真实运行环境：Windows + Python 3.12
- 包清单：以当前 `package-manifest.json` 与 `runtime-manifest.json` 的逐文件结果为准
- 姿态：`DEVELOPMENT_DIAGNOSTIC`
- 正式声明：`formalClaimsAllowed=false`
- 当前没有 `v0.4.0` release tag 或对应 package audit tag

公开仓库可以用于研究、诊断开发和本地试用；它目前不能作为 `FORMAL_GATE_READY` 的证明。

## 明确边界

- 控制器判断证据和状态是否闭合，不判断产品“是否好用”。
- Skill 安装、普通本地实验、私有运行和本地自动推进都不需要或创建私钥。
- `PASS`、零退出码或 manifest 完整不能单独证明 UI、性能、安全或发布质量。
- 本项目不是许可、收费、私钥托管或软件分发平台。

## 仓库结构

```text
.
├─ README.md          # 面向用户的仓库入口
└─ skill/             # 可独立安装与校验的 vibe-control Skill 包
   ├─ SKILL.md
   ├─ agents/
   ├─ assets/
   ├─ references/
   ├─ scripts/
   └─ package-manifest.json
```

## 深入阅读

- [Skill 路由与核心边界](skill/SKILL.md)
- [关键目标机制](skill/references/key-objectives.md)
- [检查点契约](skill/references/checkpoint-contract.md)
- [任务控制](skill/references/task-control.md)
- [自动推进与 Git 副作用边界](skill/references/automation-advancement.md)
- [跨宿主多智能体路由](skill/references/multi-session-routing.md)
- [本地进度账本与仪表台](skill/references/progress-dashboard.md)
- [实现、执行和审核角色边界](skill/references/execution-routing.md)
- [证据政策](skill/references/evidence-policy.md)
- [控制器保证闭包](skill/references/controller-assurance.md)

## 反馈问题

请在 [Issues](https://github.com/jiay98528-dev/vibe-control/issues) 中提供：精确 commit、宿主/模型、复现步骤、原始输出、预期行为，以及问题影响的检查点或声明等级。不要仅用严重度标签扩大当前任务范围。

## 许可

当前仓库尚未提供 `LICENSE` 文件。仓库公开不代表自动授予再分发或商业使用许可；如需明确许可，请先联系维护者。
