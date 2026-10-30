# 不集成 minicodex，借鉴其设计自研 agent 运行时

Status: Accepted（2026-10-30）；会话持久化方式由 ADR-0004 修订，工具执行边界由 ADR-0006 修订。

minicodex（D:\project\minicodex）是 Python 多智能体 CLI 运行时，含 agent 循环、文件工具、会话文件持久化、记忆、压缩、teammate、MCP、cron 等能力。决定：**不作为依赖集成**（不 import、不 uv path 依赖、不复制代码），而是把其中对 ai-code 场景有用的**功能设计**选择性落地为项目内自研的代理运行时（Agent Runtime）。

原因：产品自包含、无外部运行时同步负担；运行时按 ai-code 的 web 场景裁剪（如无交互审批、无 TUI）；代码全部自有，既是维护对象也是作品。minicodex 仅作为设计参考。否决的选项：uv path 依赖 minicodex（外部运行时耦合两个仓库）；vendor 复制（代码分叉后同步成本高）。

## 作用域（Round 2 定案）

- **落地**：agent 循环（多轮工具循环）、文件工具（read/write/edit/glob）、受控构建工具、会话持久化 + 上下文压缩、跨会话记忆、subagent（子代理拆分）；其中会话持久化改由 ADR-0004 使用 MySQL 实现，任意 bash 不落地
- **不落地**：teammate 多智能体协作、worktree、MCP 连接、cron、plan gate 审批、TUI/CLI 界面

## 并发模型（Round 2 定案）

单进程 + **per-app 运行时实例**：工具 cwd 绑定该 app 的 `tmp/codeOutput/{type}_{appId}`，同一 app 的请求串行、不同 app 并发。不引入子进程编排；接口层预留升级为 per-app 子进程 worker 的余地。

## 代码来源策略（Round 2 定案）

**设计借鉴、实现全重写**：不复制 minicodex 代码，按项目自身风格实现，README 标注设计参考来源。运行时全部为自有代码。
