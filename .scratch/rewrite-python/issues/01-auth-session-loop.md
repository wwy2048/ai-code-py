# 01: 完整登录会话闭环

**What to build:** 用户能够注册、登录、刷新后保持登录并退出；未登录访问受保护能力时得到统一的 `40100` 响应。现有用户使用旧密码可以登录，成功后密码自动升级为 Argon2id。该切片同时建立可运行的 Python 后端、MySQL/Redis 连接、数据库迁移基线和前端认证接入。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 后端能够连接现有 MySQL 与 Redis 并启动，数据库迁移基线不会重建或破坏现有表。
- [ ] 注册、登录、获取当前用户、退出四个流程均可从前端完整操作，cookie Session 在刷新后保持。
- [ ] 现有 MD5 用户可登录，成功后密码升级为 Argon2id；旧 Spring Session 不兼容时用户重新登录即可。
- [ ] 成功响应统一为 `{code, data, message}`；未登录返回 `40100`，不会出现裸异常或 HTML 错误页。
- [ ] 用户账号、密码长度、重复账号和越权角色等校验由后端完成，并有 HTTP seam 测试。
