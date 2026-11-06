# ai-code-py

Python 全量重写 ai-code 的后端与 Agent Runtime。

## 本地启动

1. 复制 `.env.example` 为 `.env`，填写 MySQL 与 Redis 连接。
2. 安装依赖：`uv sync`
3. 执行数据库基线：`uv run alembic upgrade head`
4. 启动后端：`uv run ai-code-py`（地址、端口和日志级别由 `SERVER_HOST` / `SERVER_PORT` / `LOG_LEVEL` 控制；开发需要热重载时用 `uv run uvicorn app.main:create_app --factory --reload`）
5. 前端：`cd frontend && npm ci && npm run dev`

后端 API 前缀为 `/api`，健康检查为 `/api/health/check`。

## 项目文档

- [重构开发复盘](docs/development-retrospective.md)
- [领域术语](CONTEXT.md)
- [生成运行时分层架构](docs/architecture/generation-runtime.md)
- [架构决策记录](docs/adr/)

## 当前能力

- 用户注册、登录、Redis Session、退出与旧密码升级
- App 创建、分页查询、详情、改名和删除清理
- 根据初始需求路由 HTML / MULTI_FILE / VUE_PROJECT
- 旧 App 通过 `/api/static/{codeGenType}_{appId}/` 预览
- 真实 DeepSeek HTML / MULTI_FILE Agent 流式工具循环、结构化 POST SSE、requestId 去重与断线续传
- 事件驱动生成运行时：`app/generation` 按 domain / ports / application / adapters 分层，每个 App 独立收件箱与常驻 worker，通过端口适配模型、MySQL、工具和 SSE
- 双向事件总线：用户消息与 interrupt 走同步入站投递，Turn 与队列事实走出站订阅；每个 App 一个 GenerationAgent，队列编辑保持同步收件箱操作
- 运行中支持插话、停止和安全转向；控制接口为 `POST /api/app/chat/turn/control`，前端提供最小控制入口
- 运行中支持 Followup 排队、Steer、单条删除、暂停队列组级 Resume 与 App 级队列 SSE；用户消息只在后端消费排队项时落库
- 模型调用耗时、token、状态与脱敏错误摘要记录，并区分生成调用和压缩调用
- `chat_history` 保存 user / assistant / tool 运行时消息，并区分 visible / active
- 根据 provider token usage 在 Turn 边界自动压缩旧上下文，保留最近完整 Turn 和隐藏摘要
- 一键部署 HTML / MULTI_FILE / Vue dist，复用稳定 deployKey 并提供公开分享地址
- 部署后异步使用无头 Chrome 截图、上传 COS 并更新应用封面，失败不影响部署
- 应用所有者可下载 HTML / MULTI_FILE / Vue Workspace 源码 ZIP
- 管理员可治理用户、全部应用、精选优先级与违规 App 清理
- 管理员可按用户/App/类型/时间检索会话，并查看 Turn 隐藏工具与模型调用观测
- M2 应用级持久记忆可开关、查看和清除，严格按 App 隔离并受上下文预算限制
- M2 一级有界子代理可在受控子目录内顺序执行局部任务，并只向主 Agent 返回摘要

未配置 `ANTHROPIC_API_KEY` 时，类型路由会安全回退为 HTML，应用仍可正常启动，但生成请求会返回可读失败。

真实模型冒烟：

```bash
ANTHROPIC_API_KEY=... uv run python scripts/smoke_html_generation.py
```
