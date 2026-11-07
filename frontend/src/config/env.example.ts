/**
 * 环境变量配置说明
 *
 * 在项目根目录创建 .env.local 或 .env.development 文件，并添加以下配置：
 *
 * # 应用部署域名
 * VITE_DEPLOY_DOMAIN=http://localhost:8123/api/deploy
 *
 * # API 基础地址（开发环境建议使用相对路径，由 Vite 代理到后端）
 * VITE_API_BASE_URL=/api
 *
 * 可视化编辑要求预览 iframe 与主站同源。生产环境应由同域反向代理
 * 同时提供前端页面和 /api 路径，不建议把 VITE_API_BASE_URL 配成跨域地址。
 */

export {}
