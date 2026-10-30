# 采用方案 A：新建 Python 项目重写 ai-code

原 ai-code 为 Java 21 / Spring Boot + LangChain4j 的单体应用，LLM 接入层仅占代码量约 15%，其余为认证、CRUD、部署、预览、截图、COS 等业务面。经三方案对比（Java 保留 + Python 网关桥 / Python 全量重写 / minicodex 移植到 Java），决定新建独立项目 `ai-code-py`，以 Python 全量重写后端，前端复用零改动，数据库 schema 沿用原 MySQL `aicode` 库不迁移。

否决的选项：Java 保留 + Python 网关桥（双栈维护 + 跨进程协议成为永久债务）；minicodex 移植到 Java（等于再造一个 LangChain4j，周期以月计）。选择全量重写的原因：用户职业方向为 agent 开发工程师，单 Python 栈的维护性与作品价值均优于混合双栈。
