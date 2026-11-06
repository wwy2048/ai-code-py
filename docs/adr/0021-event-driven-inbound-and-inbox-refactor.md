# 生成上下文改造为双向事件总线与同步收件箱操作

当前生成链路只有上行事件真正经过 `EventBus`：模型增量、工具和 Turn 生命周期由 runtime emit 到订阅者；下行用户消息、插话、打断和队列操作仍是 API 直接调用 `GenerationRuntime` 方法。App 队列 SSE 也由 `AppSession` 自己维护事件缓冲，绕过了事件订阅机制。结果是同一类用户意图有两套命令对象、两套 ACK，以及 runtime 需要拦截 Turn 事件再反向生成队列事件。

本次改造把用户消息与中断恢复为入站事件，把生成代理、收件箱和投影拆成明确边界；队列编辑保持同步方法，不进入异步消息总线。

## Status

Accepted（2026-11-06）。已完成实现并通过全量回归。

## Event Model

统一使用不可变类型化事件层次：

```text
Event
├─ UserMessage(intent=followup|steering, priority=100)
├─ UserInterrupt(stop|redirect?)
├─ StreamEvent(只允许 Mailbox 消费者)
├─ TurnLifecycleEvent
└─ QueueProjectionEvent
```

- 每个事件有稳定的 `event_id`、`type`、`app_id`、`occurred_at` 和不可变 `payload`。
- `UserMessage` 额外携带 `user_id`、`code_gen_type`、`text`、`intent`、`priority`；它的 `event_id` 同时是排队项和消费幂等标识。
- 高频流事件使用 `StreamEvent` 子类，并由类自身声明 `HANDLER_SHAPE = Mailbox`。
- 事件类型到消费形状的校验发生在订阅进入总线时；普通 lifecycle handler 与 mailbox handler 不能混用。

## Bidirectional Event Bus

`EventBus` 明确分两条方向：

- inbound：`publish(event, to=app_id)`，同步调用已注册 sink，只投递、不入库、不跑 Turn。
- outbound：`emit(event)`，异步分派订阅者；Mailbox 通过非阻塞 `offer` 接收，普通 handler 只允许做微秒级接收。

API、定时任务和其他入口不得直接调用 Agent 的业务方法投递用户消息。

## Per-App Generation Agent

`GenerationRuntime` 只保留全局职责：持有 EventBus、`AgentRegistry` 和进程生命周期。

每个 App 对应一个 `GenerationAgent`，以 `app_id` 注册 sink：

```text
GenerationRuntime
├─ EventBus
└─ AgentRegistry: app_id -> GenerationAgent

GenerationAgent
├─ followup inbox
├─ steering inbox
├─ paused inbox
├─ active turn / control bypass
└─ per-app worker
```

这样 `app_id` 同时是路由键和状态边界；同一 App 串行执行，不同 App 可并行；未来可把单个 App Agent 迁移到远程 worker 而不改变事件路由。

## Inbox Keeps The Original UserMessage

收件箱直接持有原始 `UserMessage`，不再复制为 `QueuedFollowup`、`ReadyTurn`、`StartTurnCommand` 多层对象。

```text
followup inbox ──promote──> steering inbox ──worker──> TurnRequest(user_message=event)
      │                          │
      └──────delete──────────────┘
```

- `followup`：默认去向，当前 Turn 结束后作为新 Turn 消费。
- `steering`：当前 Turn 的模型步边界 drain；空闲时降级成新 Turn。
- `paused`：因中断或失败停止自动消费的 followup 集合，由队列级 Resume 恢复。
- 消息 ID 从发布、排队、提升、暂停到消费全程不变。
- 同一类内部按 `(priority, arrival_sequence)` 排序；类序固定为 steering 优先于 followup。

## Queue Operations Stay Synchronous

`promote / delete / resume / clear_paused` 是对收件箱的同步结构操作，不走 `EventBus.publish`，也不进入异步消息队列。

同步方法在同一调用栈内完成：

1. 检查消息是否仍处于允许状态；
2. 移动或删除原 `UserMessage`；
3. 生成 `QueueMutationResult`，包含 ACK 与最新快照；
4. 同步更新 `QueueProjection`；
5. 唤醒或保持 worker，但不在调用中执行 Turn。

禁止在同步方法内部使用 `create_task(bus.emit(...))` 伪造异步通知。

## Queue Projection

新增 `QueueProjection`，而不是让 `AppSession` 直接维护前端事件缓冲：

- 对同步队列操作，通过 `apply_mutation(result)` 在同一临界区内更新快照。
- 对正式出站事件，作为 EventBus 订阅者接收消息提交和消费事实。
- 维护普通队列、暂停队列、版本号和 SSE 重放缓冲。
- 提供 App Queue SSE 快照与增量事件。

`USER_MESSAGE_COMMITTED` 与 `STEERING_CONSUMED` 作为正式出站事件由投影消费；runtime 不再拦截 Turn 事件后手动生成队列事件。

## API Adapters

保留现有 HTTP 接口作为传输适配器，不保留旧内部命令模型：

| 接口与动作 | 迁移后路径 |
|---|---|
| `POST /chat/gen/code` | `bus.publish(UserMessage(intent=followup))` |
| `turn/control: steer` | `bus.publish(UserMessage(intent=steering))` |
| `turn/control: interrupt` | `bus.publish(UserInterrupt(stop))` |
| `queue/control: enqueue` | `bus.publish(UserMessage(intent=followup))` |
| `queue/control: steer/delete/resume/clear` | 同步调用 Inbox 方法 |

`redirect` 在确认产品不再需要后从新模型移除；兼容端点可暂时返回明确的废弃/不支持结果。

## Stream Mailbox And Coalescing

- `TurnJob` 保持原始事件的有界 replay buffer，并通过 Mailbox 接收事件。
- SSE transport 作为 Mailbox 消费者，在编码前按字符阈值或帧界合并文本增量。
- 合并不得改变 `Last-Event-ID` 的重放语义，也不得丢弃非流事件。
- `emit` 热路径只调用非阻塞 `offer`，渲染在消费者侧完成。

## Commands Cleanup

迁移完成后删除以下遗留对象：

- `InboundCommand`
- `SteerTurnCommand`
- `InterruptTurnCommand`
- `RedirectTurnCommand`
- `ControlCommand`
- `EnqueueFollowupCommand`
- `SteerQueuedCommand`
- `DeleteQueuedCommand`
- `ResumeFollowupsCommand`
- `ClearPausedFollowupsCommand`
- `QueueCommand`
- `QueuedFollowup`
- `ReadyTurn`

保留并重新定位：

- `StartTurnCommand` 改名为 `TurnRequest`，只作为执行器端口 DTO，不作为入站命令。
- `CommandAck` 与 `QueueAck` 收敛为统一的命令/操作结果类型。

## Migration Phases

1. 建立新事件层次、EventBus 双向接口与订阅形状校验，同时保留旧 API 行为。
2. 增加 AgentRegistry 与 per-App GenerationAgent，将 sink 注册到总线。
3. API 适配层把用户消息和 interrupt 改为 `publish`；queue 操作改为同步 Inbox 方法。
4. worker 改为直接消费原始 `UserMessage`，移除 `QueuedFollowup/ReadyTurn/StartTurnCommand` 转换链。
5. 引入 QueueProjection，移除 `AppSession.queue_events` 与 runtime 的事件拦截。
6. 为 `StreamEvent` 接入 Mailbox 与 SSE 合并缓冲。
7. 删除遗留 command 对象、旧分支和重复 ACK。
8. 完成全量回归与真实 SSE 冒烟后，把本 ADR 转为 Accepted。

## Verification Matrix

- `publish` 调用立即返回，执行发生在 worker task 中。
- followup、steering、interrupt 分别进入正确通道。
- steering 类优先于 followup；同优先级 FIFO。
- 同步 promote/delete/resume/clear 不经过总线，不创建异步 task。
- 同一 UserMessage 在移动、暂停、消费过程中 ID 不变。
- QueueProjection 对同步操作和其他标签页事件保持一致。
- StreamEvent 只接受 Mailbox；合并后文本不丢失，生命周期事件顺序不变。
- 用户消息在消费时恰好落库一次。
- 现有生成、控制、历史、压缩、部署相关回归全部通过。

## Consequences

- 下行入口与内部状态转换从 runtime 方法调用迁移到事件投递，API 与服务解耦。
- 收件箱成为用户消息排队事实的唯一内存来源，减少对象复制和状态漂移。
- 队列操作保持同步，避免把结构性编辑误建模为异步消息。
- QueueProjection 成为 UI 的唯一队列读模型，SSE 不再与收件箱实现耦合。
- 删除大量旧 command 类后，新增用户意图只需增加事件类型和对应消费者。
