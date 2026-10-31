# ai-code-py 重构开发复盘

## 1. 文档目的

本文复盘 `ai-code-py` 从 Java/Spring Boot + LangChain4j 迁移到 Python + 自研 Agent Runtime 的实施过程，重点记录：

- 实际采用的开发范式与工作流
- 十五张 Ticket 的推进方式
- 遇到的关键问题及解决路径
- 为完成重构所做的重要工程工作
- 已验证的边界、已知限制和后续演进建议

这是一份工程实践复盘，不是 API 手册。业务概念以 `CONTEXT.md` 为准，关键架构决策以 `docs/adr/` 为准，原始需求以 `.scratch/rewrite-python/spec.md` 为准。

## 2. 最终结果概览

项目完成了从空仓库到可运行产品的完整闭环：

- 后端：Python 3.12、FastAPI、SQLAlchemy 2.0、Alembic、Redis、MySQL、uv
- 前端：Vue 3 + TypeScript，源码纳入本仓库并允许持续改造
- Agent Runtime：项目内自研，不依赖 LangChain4j，不复制 minicodex 代码
- 数据真相：`chat_history` 持久化会话，不使用 JSONL
- 三种生成类型：HTML、MULTI_FILE、VUE_PROJECT
- 产品能力：生成、预览、部署、分享、截图、COS、源码下载、管理员治理、会话观测、持久记忆、一级子代理

最终工程状态：

| 指标 | 结果 |
| --- | --- |
| 完成 Ticket | 15 |
| 后端测试 | 106 passed, 3 skipped |
| 跳过原因 | Windows 环境不允许创建符号链接 |
| 静态检查 | `ruff` 通过 |
| 类型检查 | `mypy` 通过 |
| 前端生产构建 | `npm run build` 通过 |
| 数据库迁移 | `0008_app_memory_turn_unique` |
| 当前工作区 | clean |

## 3. 开发范式

### 3.1 文档驱动，而不是直接写代码

开发从需求澄清和领域建模开始，而不是先搭脚手架：

1. 用 grilling 方式逐项确认不确定点。
2. 输出 `spec.md`，明确目标、边界、非目标和测试 seam。
3. 更新 `CONTEXT.md`，统一领域术语。
4. 用 ADR 固化不可轻易回退的架构决策。
5. 将 spec 拆成 15 张有依赖关系的垂直 Ticket。

这种方式解决了三个问题：

- 避免“重写”退化成逐行翻译旧 Java 代码。
- 避免前后端对同一概念使用不同语义。
- 避免实现到后期才发现关键约束没有决策。

### 3.2 垂直切片推进

每张 Ticket 都以可观察行为为单位，而不是按“写 model、写 service、写 controller”横向推进。一个切片通常同时包含：

- API 契约
- service/runtime 实现
- 数据库迁移
- 前端接入
- HTTP seam 测试
- 文档与环境变量更新

这样每个提交都能独立说明“用户获得了什么能力”，而不是只说明“新增了几个类”。

### 3.3 TDD 与固定 seam

项目预先定义了三个主要测试 seam：

- **S1 HTTP/SSE API seam**：从 FastAPI 公开契约验证鉴权、CRUD、SSE、分页和错误信封。
- **S2 模型调用 seam**：用脚本化模型或 mock Anthropic transport 验证工具循环、持久化、压缩和子代理。
- **S3 命令执行 seam**：替换构建进程，验证白名单、超时、输出截断和错误回传。

大多数功能按以下顺序推进：

1. 先写失败测试，固定外部行为。
2. 实现最小闭环。
3. 修复失败路径和边界。
4. 重构重复逻辑。
5. 全量测试、类型检查、前端构建。
6. 提交。

测试不只验证“成功路径”，还重点验证：

- 越权
- 重复请求
- 空数据与不存在
- 超时
- 取消/断线
- 并发冲突
- 路径逃逸
- 敏感信息泄漏

### 3.4 Standards / Spec 双轴 review

每个 Ticket 完成后并行执行两类 review：

- **Standards**：仓库编码规范、可维护性、Fowler smell、类型与模块边界。
- **Spec**：是否忠实实现 Ticket/ADR，是否有缺失、错误或范围蔓延。

两轴有意分开，避免“代码写得很规范但产品行为错了”或“功能正确但工程结构退化”互相掩盖。

本次 review 不是形式化流程，实际发现了多个关键问题，例如：

- 工具批次恢复时丢失 assistant 文本
- MySQL `DATETIME(0)` 不适合作为部署版本条件
- 管理列表仍返回隐藏正文
- 管理员编辑他人 App 仍调用所有者详情接口
- 真实 Anthropic 模型未使用动态工具清单
- 并发部署可能让旧截图覆盖新部署

### 3.5 每个里程碑独立提交

每个 Ticket 完成后形成独立提交，提交信息说明用户能力而非文件变更。数据库迁移、前端适配和测试与对应能力一起提交，确保提交点可测试、可回滚、可审计。

## 4. 关键架构决策

### 4.1 自研 Agent Runtime

没有把 LangChain4j 翻译成某个 Python Agent 框架，也没有把 minicodex 当依赖。运行时由项目自己实现：

- 模型消息协议
- 多轮工具循环
- 工具注册与分发
- Turn 生命周期
- 会话持久化
- 上下文恢复与压缩
- 持久记忆
- 有界子代理

这使运行时行为、错误边界和数据模型都可控，也为后续学习/展示 Agent 工程能力留下了真实代码。

### 4.2 MySQL 是会话唯一真相

`chat_history` 同时承载 UI 可见消息和运行时消息，通过字段区分：

- `visible`：是否展示给普通用户
- `active`：是否进入后续模型上下文
- `message_kind`：user / assistant / tool_call / tool_result / compaction
- `payload`：结构化运行时元数据
- `turn_id` / `sequence`：Turn 归属与稳定顺序
- `compacted_by`：被哪条摘要覆盖

这样不需要 JSONL，也不会出现“数据库历史”和“文件会话”两套真相。

### 4.3 结构化 POST SSE

生成协议使用 `POST /api/app/chat/gen/code`，请求包含 `appId`、`message`、`requestId`。服务端返回结构化 SSE：

- `turn_started`
- `text_delta`
- `tool_started`
- `tool_finished`
- `compaction`
- `subagent_started`
- `subagent_finished`
- `subagent_failed`
- `turn_finished`
- `error`

同时支持 `requestId` 去重和 `Last-Event-ID` 续传。客户端断开后任务继续执行，生成结果仍会落入 MySQL。

### 4.4 受控工具而不是任意 shell

模型只能使用受控文件工具和受控构建工具：

- read_file
- write_file
- edit_file
- glob
- Vue build_project
- delegate_subagent

没有任意 bash、npm 安装或下一次级子代理。Workspace 统一负责路径根、符号链接和文件大小校验。

### 4.5 前端作为可演进源码

前端先复制可构建基线，再按新协议直接修改。没有要求旧接口逐字节兼容，因此可以做：

- POST SSE 流式读取
- 稳定游标加载历史
- Turn 调试详情
- 部署分享弹窗
- 记忆查看/清空
- 管理员治理页面

## 5. 重要工作分解

### 5.1 基础设施与认证

- FastAPI 应用工厂与统一错误信封
- SQLAlchemy async engine、Alembic 基线
- Redis Cookie Session
- Argon2id 密码哈希与旧 MD5 密码自动升级
- 用户/应用表兼容现有 MySQL 数据

### 5.2 应用与生成路由

- App CRUD、所有权校验、分页和删除清理
- `codeGenType` 统一为 `html` / `multi_file` / `vue_project`
- DeepSeek Anthropic 兼容端点接入
- 真实模型类型路由与失败回退

### 5.3 Agent Runtime 核心

- HTML / MULTI_FILE / Vue 共用工具循环
- 工具 schema 与 handler 分离
- 动态工具清单真正传入模型协议
- 工具调用批次 `batchId` 与 assistant 文本持久化
- 工具调用/结果配对恢复
- 模型调用耗时、token、状态、重试与脱敏错误记录

### 5.4 会话恢复与压缩

- 按 App 加载 active 历史
- 保留最近完整 Turn
- 工具批次不被拆散
- provider usage 触发压缩
- 旧消息标记非 active，不物理删除
- 隐藏 Compaction Summary 注入后续上下文
- 二次压缩只合并旧摘要与新增被覆盖 Turn

### 5.5 三类代码生成

- HTML：单文件页面
- MULTI_FILE：index/style/script 三文件结构
- VUE_PROJECT：固定可信模板，仅允许修改 `src/App.vue` 和 `src/style.css`
- Vue 构建在受控子进程中执行，失败日志回传模型并限制修复轮数

### 5.6 部署、分享与封面

- 稳定 6 位 deployKey
- 首次生成、后续复用
- HTML/MULTI_FILE 复制 Workspace，Vue 复制 dist
- 临时目录复制后整体替换，避免半更新
- 部署串行锁和 `deploy_revision` 条件更新
- 异步无头 Chrome 截图、COS 上传和 cover 更新
- 截图失败不影响部署

### 5.7 源码下载

- 所有者鉴权
- 三类项目保留相对结构
- 过滤 node_modules、dist、编辑器目录和临时文件
- 拒绝 symlink/junction 和 Workspace 外文件
- 文件数、未压缩体积和扫描异常限制

### 5.8 管理员治理与观测

- 数据库角色决定管理员身份
- 用户分页、详情、角色/资料更新、逻辑删除
- 跨用户 App 查询、详情、更新和删除
- 精选状态统一为 `priority > 90`
- 管理员会话列表和 Turn 详情
- 隐藏 Tool/Compaction 可观测，但正文截断
- API Key、Bearer、token/password 等写入前和读取时双重脱敏

### 5.9 M2 持久记忆与子代理

持久记忆：

- 默认关闭
- App + 用户双重隔离
- 成功 Turn 才提取
- 目标和结果分别限长
- 总注入预算包含标题和分隔符
- `(appId, source_turn_id)` 唯一约束

子代理：

- 一级、顺序执行
- 受控子目录
- 无 shell、无提问、无嵌套
- 最大轮数和超时
- 同 App 共享锁
- 主 Agent 只接收摘要
- started/finished/failed 事件可见

## 6. 遇到的主要问题与解决方式

### 6.1 “重写”容易变成逐行翻译

**问题**：旧项目使用 LangChain4j 和 Spring 生态，如果按 Java 类逐个翻译，会把旧抽象和副作用一起搬过来。

**解决**：先写 spec 和 ADR，只保留产品行为和安全约束；运行时、消息模型、持久化方式重新设计。

### 6.2 旧 MySQL 数据不能重建

**问题**：要求保留原 `user` / `app` / `chat_history` 数据。

**解决**：增量 Alembic 迁移，不做破坏性重建；兼容旧 `messageType` / `message`；用 `message_kind` 等新字段承载运行时语义。

### 6.3 SQLite 测试与 MySQL 行为不同

**问题**：MySQL `DATETIME(0)` 不保留微秒，曾导致用 `deployedTime` 做并发校验在测试中通过、生产会失败。

**解决**：新增单调 `deploy_revision`，封面任务按版本条件更新；迁移在真实 MySQL 上执行验证。

### 6.4 多工具调用的上下文恢复不忠实

**问题**：工具调用与结果逐行持久化后，恢复时容易拆成多组 assistant/result，或丢失工具调用前的 assistant 文本。

**解决**：写入 `batchId` 和 `assistantText`，恢复时按批次重建一个 assistant 多 tool_calls 消息，再输出全部 result。

### 6.5 时间相同导致分页重复/跳过

**问题**：只按 `createTime` 做游标，同一秒多条消息会重复或漏读。

**解决**：游标升级为 `(createTime, id)` 复合条件，前端保持滚动位置并传递 `lastId`。

### 6.6 压缩边界难定义

**问题**：简单按消息数截断会破坏工具配对，也可能把当前 Turn 截掉。

**解决**：以 Turn 为单位，找到最近完整 Turn，只压缩其之前的完整记录；摘要和剩余 active 消息重新投影，摘要永远置顶。

### 6.7 异步截图与部署的一致性

**问题**：截图可能较慢或失败；并发重新部署时旧截图可能覆盖新封面；服务关闭时后台任务可能丢失。

**解决**：部署先提交再调度；后台任务记录 `deploy_revision`；条件 UPDATE 只允许当前部署写入；关闭时有限时等待并取消超时任务。

### 6.8 部署与下载的路径安全

**问题**：模型生成内容属于不可信输入，Workspace 中可能出现 `..`、symlink、Windows junction 或特殊文件。

**解决**：统一 Workspace 根校验；部署和下载均使用 `follow_symlinks=False` 的逐项遍历；拒绝链接、越界、超大文件/目录；ZIP 只写相对路径。

### 6.9 敏感信息可能进入日志

**问题**：模型异常文本可能包含 API Key、Bearer token、URL 凭据。

**解决**：`ModelCallLog` 写入前统一脱敏，管理员读取时再次脱敏；工具调用只暴露 path/pattern，隐藏正文最多 500 字预览。

### 6.10 测试容易被“假实现”误导

**问题**：只测 fake model 可能漏掉真实 Anthropic transport 不使用动态工具清单的问题。

**解决**：增加 transport seam 测试，直接检查实际发给模型的 `tools`、消息块和 usage；只有少量纯逻辑使用单元测试。

### 6.11 Windows 开发环境限制

**问题**：部分符号链接测试无法创建；uv 默认源曾长时间卡住。

**解决**：保留可跳过的真实链接测试，同时用 monkeypatch 和底层 helper 覆盖异常分支；uv 固定使用阿里云镜像。

## 7. 做得好的地方

1. **先定义边界，再写实现**：安全、持久化、工具和事件语义都在编码前有文档。
2. **所有重要状态都有明确归属**：MySQL、Redis、Workspace、deploy root、截图目录职责清晰。
3. **错误路径与成功路径同等重要**：越权、失败、超时、断线、并发和清理都有测试。
4. **外部副作用尽量可恢复**：临时目录部署、版本条件更新、best-effort 清理、失败不回滚已成功部署。
5. **review 能真正发现问题**：双轴 review 多次发现生产级边界问题，而不是只检查格式。
6. **可观测性不是后补**：模型调用日志、SSE 事件、管理员 Turn 详情从运行时设计阶段就存在。
7. **功能开关保护 M2 能力**：持久记忆和子代理默认关闭，不影响主链路。

## 8. 已知限制

- 单进程部署起步，部署锁和子代理锁是进程内锁；多 worker 需要数据库锁或分布式协调。
- 本地文件系统不是容器隔离；当前安全边界依赖工具白名单和路径校验。
- 真实截图依赖 Chrome 和 Selenium Manager；不可用时只会导致封面缺失，不影响部署。
- 记忆提取目前是确定性拼接与截断，还不是模型级语义压缩。
- 子代理只支持一级、顺序执行；没有并行写入、独立 worktree 或嵌套。
- 旧接口只有产品流程兼容，不承诺逐字节兼容。

## 9. 后续建议

优先级建议：

1. 增加 Docker/容器化隔离，解决任意构建和依赖安装的长期边界。
2. 多实例化后把部署锁、子代理锁升级为数据库/Redis 锁。
3. 把记忆提取升级为可审计的模型压缩流程，保留来源 Turn 和置信度。
4. 为管理员观测增加独立审计日志，覆盖删除、角色变更和封面重生成。
5. 增加真实浏览器 CI（使用容器 Chrome）与 COS 集成测试。
6. 对 SSE 事件 schema 做版本化，降低前端与后端演进耦合。
7. 引入前端单元测试，覆盖滚动分页、Turn 详情和错误信封展示。

## 10. 可复用检查清单

面对类似“旧系统全量重写 + Agent 能力”的项目，可复用以下清单：

- [ ] 是否先写 spec、领域术语和 ADR？
- [ ] 是否明确数据库/文件/对象存储的唯一真相？
- [ ] 是否定义 S1/S2/S3 测试 seam？
- [ ] 是否把模型输出视为不可信输入？
- [ ] 是否限制 Workspace、工具、shell、子代理和网络副作用？
- [ ] 是否让 SSE 事件结构化并支持去重/续传？
- [ ] 是否把隐藏运行时消息与用户可见历史分离？
- [ ] 是否用 Turn/批次保证恢复不拆断工具调用？
- [ ] 是否有上下文预算、压缩和失败回退？
- [ ] 是否把敏感信息在写入和读取两端都脱敏？
- [ ] 是否对部署、截图、上传和删除做幂等与补偿设计？
- [ ] 是否执行 Standards/Spec 双轴 review？
- [ ] 是否在真实 MySQL 和真实模型/构建链路上做最终冒烟？

## 11. 结论

这次重构的核心不是“把 Java 换成 Python”，而是重新建立了一套适合 Agent 产品的工程边界：

- 文档负责定义目标和语义
- Runtime 负责循环、工具和上下文
- MySQL 负责唯一会话真相
- 受控 Workspace 负责不可信代码落盘
- SSE 和模型日志负责可观测性
- 测试 seam 负责可重复验证
- 双轴 review 负责发现规范和产品语义偏差

最终交付的是一条可运行、可验证、可继续演进的全栈 Agent 代码生成平台，而不是一次框架替换。
