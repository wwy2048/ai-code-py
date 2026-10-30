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
- 脚本化 HTML Agent 工具循环、结构化 POST SSE、requestId 去重与断线续传
- `chat_history` 保存 user / assistant / tool 运行时消息，并区分 visible / active

未配置 `ANTHROPIC_API_KEY` 时，类型路由会安全回退为 HTML，应用仍可正常启动。
