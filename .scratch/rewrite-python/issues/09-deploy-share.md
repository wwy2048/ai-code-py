# 09: 一键部署与分享链接

**What to build:** 用户可以一键部署 App，获得稳定 deployKey 和可分享 URL。HTML、MULTI_FILE 使用生成目录，VUE_PROJECT 使用受控构建产物；再次部署更新同一分享地址。

**Blocked by:** 6

**Status:** ready-for-agent

- [ ] 只有 App 所有者可以部署，源 Workspace 不存在或 Vue 构建产物缺失时返回明确错误。
- [ ] 首次部署生成唯一 deployKey，后续部署复用该键并替换部署内容。
- [ ] 静态资源服务提供正确的 MIME、目录默认页和路径越界保护。
- [ ] 前端部署流程展示成功链接，并可从首页或详情页打开分享地址。
- [ ] HTTP seam 测试覆盖部署权限、重复部署、静态访问和路径逃逸。
