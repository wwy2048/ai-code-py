# 本地部署目录提供稳定分享地址

部署采用独立 `deploy_root/{deployKey}` 目录和 FastAPI 静态挂载 `/api/deploy`。首次部署生成 6 位字母数字键并持久化，之后复用同一键；每次部署先在临时目录复制完整产物，再整体替换旧目录，避免部分文件更新。HTML 与 MULTI_FILE 复制 Workspace，VUE_PROJECT 只复制已经通过受控构建生成的 `dist`。

## Status

Accepted（2026-10-31）。

## Consequences

- 分享 URL 由 `DEPLOY_URL_BASE/{deployKey}/` 组成，可通过反向代理映射到自定义域名。
- 部署只接受正在运行的完整产物；Vue `dist` 缺失时明确失败，不在部署请求中隐式构建。
- 源目录包含符号链接、缺少 `index.html` 或越出 Workspace 根目录时拒绝部署。
- 静态服务由 Starlette StaticFiles 提供目录默认页、MIME 和路径逃逸保护；部署 URL 无需登录即可访问。
- 重新部署会删除旧版本遗留文件，但保持原分享链接不变。
