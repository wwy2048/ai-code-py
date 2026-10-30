# ai-code-py

Python 全量重写 ai-code 的后端与 Agent Runtime。

## 本地启动

1. 复制 `.env.example` 为 `.env`，填写 MySQL 与 Redis 连接。
2. 安装依赖：`uv sync`
3. 执行数据库基线：`uv run alembic upgrade head`
4. 启动后端：`uv run uvicorn app.main:create_app --factory --reload --port 8123`
5. 前端：`cd frontend && npm ci && npm run dev`

后端 API 前缀为 `/api`，健康检查为 `/api/health/check`。
