# 生成运行时分层架构

## 1. 限界上下文

`app/generation` 是生成链路的限界上下文，负责：

- 接收启动、插话、停止和转向命令；
- 调度每个 App 的 Turn；
- 管理 Turn 内部控制状态；
- 执行模型/工具循环；
- 发布有序出站事件；
- 通过端口适配 MySQL、模型、文件工具和 SSE。

HTTP、数据库、Anthropic SDK、文件系统和 SSE 都不属于领域层。

## 2. 目录与职责

```text
app/generation/
├── domain/                 # 纯领域模型，不依赖 asyncio、DB、FastAPI
│   ├── commands.py         # StartTurn / Steer / Interrupt / Redirect
│   ├── events.py           # EventType / EventEnvelope
│   └── messages.py         # 模型上下文消息
├── ports/                  # 内层定义的对外契约
│   ├── model.py            # AgentModel / ModelRequest / ModelReply
│   ├── journal.py          # TurnJournal
│   ├── tools.py            # ToolExecutor / ToolExtension / ToolResult
│   └── turn.py             # TurnExecutor / TurnExecutionResult
├── application/            # 用例编排，只依赖 domain + ports
│   ├── bus.py              # 出站事件总线
│   ├── control.py          # Turn 控制状态
│   ├── hooks.py            # 强类型生命周期 Hook
│   ├── inbox.py            # App 收件箱与会话 worker
│   ├── loop.py             # 模型/工具循环
│   ├── runtime.py          # GenerationRuntime 应用门面
│   └── stream.py           # Turn 重放缓冲与 SSE 编码
└── adapters/               # 外层实现
    ├── conversation.py     # TurnJournal -> ChatHistory
    ├── conversation_store.py
    ├── executor.py         # 组装模型、工具、Workspace 和 Journal
    ├── anthropic_model.py
    ├── codegen.py
    ├── tools.py
    ├── workspace.py
    └── ...
```

## 3. 依赖规则

```text
API / SSE / Frontend
        │
        ▼
GenerationRuntime ──────► EventBus / TurnJob
        │
        ▼
AppSession / Worker / TurnControl / AgentLoop
        │
        ├──────────────► ports
        │                  │
        ▼                  ▼
     domain            adapter interfaces
                           ▲
                           │
                    generation/adapters
```

- `domain` 只依赖 Python 标准库。
- `ports` 只依赖 `domain`。
- `application` 只依赖 `domain` 和 `ports`。
- `adapters` 依赖 `application/ports/domain`，并实现外层技术细节。
- `app/api` 只把 HTTP 请求转换为命令，把出站事件转换为 SSE。
- 旧 `app/runtime` 目录已删除，生成链路不再保留旧运行时兼容层。

## 4. 一次 Turn 的数据流

```text
POST /chat/gen/code
  -> StartTurnCommand
  -> GenerationRuntime.start_turn
  -> AppSession.followup inbox
  -> SessionWorker
  -> TurnExecutor adapter
  -> AgentLoop
       ├─ model step
       ├─ tool execution
       ├── steering drain
       └─ interrupt / redirect safe point
  -> EventBus
  -> TurnJob replay buffer
  -> SSE adapter
```

控制命令走旁路：

```text
POST /chat/turn/control
  -> SteerTurnCommand / InterruptTurnCommand / RedirectTurnCommand
  -> GenerationRuntime.control
  -> active TurnControl
  -> 下一模型步边界 / 当前模型流取消
```

## 5. 事实与投影

- ChatHistory 是 Turn 事实层。
- `TurnJob` 只是进程内 SSE 重放缓冲，不是事实层。
- `turn_lifecycle` 和中断占位写入 ChatHistory。
- 超出重放窗口时发送 `stream_gap`，前端重新读取 ChatHistory。