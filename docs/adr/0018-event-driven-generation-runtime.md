# 生成链路采用事件驱动运行时

为了在同一生成会话内可靠支持追问、插话和打断/转向，决定把生成链路重构为事件驱动运行时：入站命令与出站事件分开，每个 App 使用收件箱和常驻会话工作器，打断/转向走控制旁路，事件总线只负责分发而不持有事实。本次范围限定为生成与会话控制链路，部署、截图、封面等后台能力暂不接入；MySQL 中的 ChatHistory / Turn Journal 仍是事实层，SSE 继续作为出站事件的传输适配器，现有生成 API 和事件契约只做增量扩展。

## Status

Accepted（2026-11-06）。代码落点与分层由 ADR-0019 细化并取代，事件语义继续有效。

## Consequences

- 第一版使用单进程内总线与收件箱，不引入 Redis Streams、数据库 Outbox 或事件溯源。
- V1 支持 followup、steering 和 interrupt/redirect；工具审批与人工恢复不在本阶段。
- 现有 POST 生成接口、事件名、载荷、requestId 幂等和 Last-Event-ID 行为保持兼容；新增控制能力通过增量接口和事件提供。
- 迁移采用一次性替换原生成管理链路；新旧链路不长期并行。切换前必须完成等价行为测试、并发/取消/断线测试和真实 SSE 冒烟。
- HookBus 继续承担强类型生命周期扩展，不与事件总线合并。
## Runtime Contract

第二轮决策（2026-11-06）：

- 每个 App 一个生成会话工作器，同一 App 任一时刻最多执行一个 Turn。
- 正常输入分为 followup 与 steering 两条收件箱；interrupt/redirect 不排队，走控制旁路。
- steering 只在下一次模型调用前消费；空闲时降级为新 Turn。followup 只在新 Turn 开始时消费。
- interrupt/redirect 采用协作式安全点：立即停止模型流，正在执行的工具跑完，尚未开始的工具调用记录为 interrupted；redirect 加入当前 Turn 继续执行，Turn 已结束时降级为 followup。
- 原生成接口继续承担启动与 followup；新增统一控制接口 `POST /app/chat/turn/control`，控制命令带 `commandId` 并在进程内幂等。
- 入站命令与出站事件使用类型化模型；SSE adapter 保持现有 wire 事件名和载荷兼容。
- 每个 Turn 使用有界重放缓冲，每个订阅者使用独立有界 mailbox；积压时允许合并文本增量，超出重放范围时发送 stream_gap 并以持久化历史重新同步。
- 不新增 Turn 状态表或事件表；Turn 生命周期事实作为隐藏记录追加到 ChatHistory 轨迹，由投影层解释。
## Event And Turn Semantics

以下决策由参考实现、参考文档和现有 SSE 契约确定：

- 出站事件使用类型化信封：`type`、`app_id`、`turn_id`、`step_id?`、`sequence`、`occurred_at`、`schema_version`、`command_id?`、`payload`。`sequence` 在 Turn 内从 1 连续递增，SSE `id` 继续使用该整数。
- ChatHistory 追加隐藏的 `turn_lifecycle` 记录：开始一条，completed / failed / interrupted 终态一条；记录不进入模型上下文。Step 状态只存在于 worker 内存。
- 中断时丢弃未完成的 LLM 输出，不把半截 assistant 文本写入轨迹；写隐藏 assistant 中断占位。已开始的工具等待完成，未开始的工具逐个写中断占位，保证 assistant tool_call 与 tool result 配对。
- 控制命令按 App 和 commandId 幂等。Turn 已结束时，interrupt 为 not_active，带文本的 steering/redirect 降级为 followup；目标 Turn 不匹配时为 stale。多次 redirect 按到达顺序累积，纯 stop 抢占并结束当前 Turn。
- steering 和 redirect 共用当前 Turn 的模型轮次与 token 预算；followup 开启新 Turn 和新预算。
- worker 在 App 首次收到命令时启动，之后常驻直到显式停机，不设置空闲退出。进程重启不恢复模型流或工具执行；投影层把没有终态的 Turn 解释为 interrupted。
- 一次性切换前必须通过现有测试、新状态机/控制/并发/取消/断线测试、真实模型 SSE 冒烟和前端生产构建。
## Frontend Scope

第三轮决策（2026-11-06）：本轮包含最小前端控制，不做完整控制历史控制台。

- 现有输入框继续表示 followup，保持原有发送语义。
- 运行中的生成消息提供“插话”“停止”“转向”三个最小控制入口。
- 控制结果通过现有 SSE 增量事件呈现；除 stream_gap 后的历史重同步外，不新增完整控制审计页面。
