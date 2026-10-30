# ai-code-py

Python 全量重写 ai-code 的后端与 Agent Runtime。

## 本地启动

1. 复制 `.env.example` 为 `.env`，填写 MySQL 与 Redis 连接。
2. 安装依赖：`uv sync`
3. 执行数据库基线：`uv run alembic upgrade head`
4. 启动后端：`uv run uvicorn app.main:create_app --factory --reload --port 8123`
5. 前端：`cd frontend && npm ci && npm run dev`

后端 API 前缀为 `/api`，健康检查为 `/api/health/check`。

## 当前能力

- 用户注册、登录、Redis Session、退出与旧密码升级
- App 创建、分页查询、详情、改名和删除清理
- 根据初始需求路由 HTML / MULTI_FILE / VUE_PROJECT
- 旧 App 通过 `/api/static/{codeGenType}_{appId}/` 预览
- 真实 DeepSeek HTML / MULTI_FILE Agent 流式工具循环、结构化 POST SSE、requestId 去重与断线续传
- 模型调用耗时、token、状态与脱敏错误摘要记录，并区分生成调用和压缩调用
- `chat_history` 保存 user / assistant / tool 运行时消息，并区分 visible / active
- 根据 provider token usage 在 Turn 边界自动压缩旧上下文，保留最近完整 Turn 和隐藏摘要
- 一键部署 HTML / MULTI_FILE / Vue dist，复用稳定 deployKey 并提供公开分享地址

未配置 `ANTHROPIC_API_KEY` 时，类型路由会安全回退为 HTML，应用仍可正常启动，但生成请求会返回可读失败。

真实模型冒烟：

```bash
ANTHROPIC_API_KEY=... uv run python scripts/smoke_html_generation.py
```
