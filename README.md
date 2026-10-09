# DevFlow AI

Python + FastAPI + LangGraph + Next.js 的研发协作工作台。根据[小林 DevFlow AI 公开介绍](https://xiaolincoding.com/project/devflow.html)从零实现，不含课程源码。当前为 v0.18：已有五阶段基础闭环、本地中文混合检索、签名 Webhook 队列、真实回答评审、同源 Prompt 对照、固定评估样本集、版本固定的任务 Skill，并新增手机界面、安卓客户端工程与 HTTPS 部署配置。阶段编号表示当前建设位置，不表示全部功能已验收。

## 现在能做什么

- 仓库看板：展示已同步范围内的 Issue、PR 和 GitHub Actions。
- 专用分析：Issue 分诊、PR diff 风险审查、失败 CI 日志排障。
- 动态协作：LLM Planner 生成经过字段、范围和依赖校验的任务 DAG；LangGraph 按依赖调度，限制并发和超时。失败依赖跳过，Observer 汇总缺证与版本差异，最多补查一次，保留已完成结果后汇总。
- 代码工作区：只读获取默认分支 Git 提交，按固定 SHA 列出文本文件、读取行号和检索源码。本地开发可创建当前 DevFlow 项目的独立源码快照，来源与远程提交分开标注。
- Tool Calling：真实模式下，ChatAgent 自主选择注册的只读工具，限制步数和重复调用。
- SSE：展示工具和 Agent 执行进度，保存结论、证据、提交 SHA 和完整事件记录。
- 后台任务：提交到 SQL 队列，由独立 Worker 执行。关闭页面或重启 API 不取消分析；运行记录可重新订阅，断线按事件编号重连。停止是独立的后台控制请求。
- 断点恢复：协作检查在任务结束时保存 SQL 检查点；停止或进程中断后，沿用原计划、源码、文档和已保存结果，只继续尚未产生终态记录的任务。已保存的汇总直接恢复输出。
- 项目知识：同步 README 或手动导入 Markdown，按标题切分并保存来源路径、行号和版本。使用 BM25 检索；配置 Embedding 和 Milvus 后可建立索引，使用关键词与向量混合召回、RRF 和可选 Rerank。
- 项目记忆：人工录入或从完成结论提取候选，批准后才能被 BM25 工具检索和引用；修改撤销审批，新任务固定获批版本，旧任务恢复沿用原版本。
- 草稿审批：按正文、目标和版本提交审核；修改后重新审批。GitHub 评论发布入口与模型工具隔离，默认关闭；重复请求返回保存的结果，结果不明时只核对，不自动重发。
- 权限与审计：可启用账号登录、仓库只读/编辑/审核角色和持久化操作记录；默认本地模式明确显示未验证身份。
- 源码事实核对：从固定提交的 Python 片段识别直接数值声明与特定评分公式，检查有限数值句式及分子乘数/k1+1、长度常数项/1-b 的数学关系。数值与关系分类展示；冲突移出正文，保留原文并暂停自动生成草稿，不验证其余语义或运行行为。
- 评测与交付：保存 60 个固定样例的文档、源码、数值与分析检查，比较旧/新检索策略，导出文档样例并运行隔离环境中的 Ragas 非模型指标；真实回答支持人工评审和标注导出，展示新运行的耗时与供应商用量，整理已保存分析周报，经人工确认存入知识库。
- Prompt 版本：分析前选择基线 v1 或证据优先 v2，记录实际指令与模型输入指纹；已保存回答可并排查看、附人工标注导出，同源条件不足会列明原因，质量由人工核验。
- 两版提交：一次固定来源与参数，同时入队两次分析；相同提交编号重试复用记录，容量不足时整组回滚，停止、失败与完成状态都保留。
- 任务 Skill：选择固定源码解释、PR 风险审查、CI 排障或发布前协作检查，预览流程要求；提交固定定义版本和指纹，限制可用工具，结果记录版本。PR/CI 必须填写明确目标编号。
- MCP：通过官方 Python SDK 提供 6 个 stdio 只读工具，使用独立 Token 和仓库白名单。默认未配置，不增加 Agent 的写权限。
- 数据存储：本地 SQLite，部署配置使用 PostgreSQL。会话记录持久化，live 智能问答使用最近三轮摘要作为上下文；长期记忆目前为人工批准的项目约定与经验，不包含自动超长会话压缩。

## 两种运行模式

安卓客户端与手机界面：v0.18 新增底部导航、触摸布局、HTTPS 连接页和原生 JSON 保存工程，使用当前 v0.18 后端；编译、外网部署和数据迁移说明见 [手机应用指南](docs/mobile-app.md)。云平台单服务部署见 [部署说明](infra/cloud/README.md)，服务地址预置进 APK 后，首次打开直接进入登录流程。

| 模式 | 数据来源 | 分析方式 | 配置要求 |
| --- | --- | --- | --- |
| `demo`（默认） | 自带 `demo/devflow-shop` 样例 | 确定性规则，**没有调用大模型** | 不需要密钥和 Docker |
| `live` | GitHub REST API | 支持 Tool Calling 与 JSON 输出的兼容模型服务 | 模型配置；私有仓库和 Actions 日志通常还需要 GitHub Token |

真实模式配置错误会报错，不会自动降级为演示结果。Planner 无法生成有效计划时会明确标注服务端模板降级；专用任务失败保留记录，模型汇总失败明确显示规则汇总。GitHub 同步是有范围的快照：最多一页 100 条开放 Issues（其中可能包含 PR）、100 个开放 PR 和 30 次 Actions。PR 审查最多读取 100 个变更文件，Actions 匹配 PR 的 head SHA；完整 Review、外部 CI、分支保护仍待接入。

同步列表使用 ETag 条件请求复用未变化的响应；README 按本次读取的默认分支 commit SHA 获取，引用链接固定到该提交。ETag 缓存保存在本地快照内，不返回给前端。v0.9 支持已签名事件触发的分项刷新，Webhook 默认关闭；“连接与配置”展示完整同步时间、分项时间、队列和投递结果。

## 事件同步与 GitHub Webhook（v0.9）

手动同步刷新全部有范围快照；事件刷新只更新相关列表，并复用各端点的 ETag。每次都核对 GitHub 仓库数字 ID，防止名称被其他仓库复用。事件正文仅作为刷新信号；Worker 从 GitHub 重新读取当前状态，因此延迟或乱序事件不会把旧正文写回快照。分项同步时间独立显示，不能把最近一次事件时间当作所有数据的新鲜时间。

| 事件 | 刷新范围 |
| --- | --- |
| `issues` 的打开、编辑、关闭等已支持 action | 开放 Issues 列表 |
| `pull_request` 的打开、同步、关闭等已支持 action | 开放 PR 和 Issues 列表（Issues 分页包含 PR） |
| `workflow_run` 的 requested / in_progress / completed | 最近 30 次 Actions |
| 默认分支 `push` | 仓库、默认分支 SHA 与该 SHA 的 README |
| `ping`、其他事件/action、非默认分支 push | 记录为忽略，不刷新数据 |

先手动同步目标仓库一次，记录数字 ID。在本地 `backend/.env` 设置：

```dotenv
GITHUB_WEBHOOK_ENABLED=true
GITHUB_WEBHOOK_SECRET=自行生成的独立至少32字符随机密钥
GITHUB_WEBHOOK_REPOSITORY_IDS=当前工作台中仓库的32字符ID
```

白名单使用本地仓库 ID，可逗号分隔，不接受 owner/repo。Webhook 密钥不能复用 GitHub、模型、Embedding 或 MCP 密钥。API 和 Worker 读取同一文件并一起重启。当前工作区未开启实际仓库接收，也没有注册远程 Webhook。

GitHub 仓库管理员配置接收 URL 为可信 HTTPS 服务的 `/api/webhooks/github`，内容类型 `application/json`，Secret 与本地一致，只订阅表中需要的事件。不要将整个默认本地管理接口暴露到公网：接收入口应由反向代理单独转发，应用管理界面仍需身份配置与访问控制。现有 `127.0.0.1` 地址无法接收 GitHub 推送；本轮未建立公网入口，未修改 GitHub Webhook 设置。参考 [GitHub Webhook 最佳实践](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)。

入口按原始 UTF-8 字节核对 `X-Hub-Signature-256` HMAC-SHA256，再验证仓库名称、数字 ID 和白名单；使用独立服务验签，不依赖浏览器登录。只保存投递 UUID、正文哈希、事件/action、刷新范围和处理状态，不保存原始正文、签名或密钥。[官方验签说明](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)。同一 `X-GitHub-Delivery` 重发返回原处理状态，正文或事件类型冲突拒绝，不重复入队。

收到事件并持久化后返回 202，独立 Worker 执行只读 GitHub 请求，不发起分析或模型调用。手动同步与事件同步共用仓库租约，旧进程失去租约后不能覆盖快照。服务中断后队列仍保留；读取中断可重新领取，最多 3 次。远程读取失败保留原快照，并显示失败；编辑者可在页面重试。该按钮只重试已经接收的刷新任务，GitHub 未成功投递的事件仍需在 GitHub 重新投递，或手动完整同步。[GitHub 重新投递说明](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks)。

开发容量为 1 MB 未压缩正文、100 个待处理事件、10000 条历史记录；超过上限拒绝接收。当前没有自动定期全量对账、组织/GitHub App 事件、仓库重命名迁移、自动拉取全部源码或全仓文档。事件仅刷新有范围快照，不代表完整仓库活动；已保存运行继续使用原证据版本。页面可点击“读取最新看板数据”载入已完成的后台刷新。

## 本地启动（Windows PowerShell）

要求 Python 3.12+、Node.js 24 与 Git。首次安装前确认 `python --version` 为 3.12 或更新版本；已经安装的项目使用 `.venv\Scripts\python.exe`。

第一次安装，在项目根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock.txt
if (!(Test-Path backend\.env)) { Copy-Item backend\.env.example backend\.env }
Set-Location frontend
npm ci
```

打开三个终端，均从项目根目录启动；先启动 FastAPI，再启动 Worker：

```powershell
# 终端 1：FastAPI
.\scripts\start-backend.ps1
```

```powershell
# 终端 2：Next.js
.\scripts\start-frontend.ps1
```

```powershell
# 终端 3：独立后台 Worker
.\scripts\start-worker.ps1
```

访问 [http://127.0.0.1:3000](http://127.0.0.1:3000)。API 文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。停止服务：在对应终端按 Ctrl+C。

如果终端脚本受本机执行策略限制，可直接启动：

```powershell
# 终端 1
Set-Location backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
# 终端 2
Set-Location frontend
npm run dev
```

```powershell
# 终端 3
Set-Location backend
..\.venv\Scripts\python.exe -m app.worker
```

API 与 Worker 必须在同一 `backend` 目录运行并共享 `.env`、数据库及代码缓存。Worker 未启动时，任务保留为“排队中”。

## 演示顺序

1. 概览页点击“运行发布检查”。
2. 查看“暂缓发布”的结论：Issue #42 描述跨租户风险，PR #18 移除租户条件，CI #301 的测试实际返回 200，预期是 404。
3. 核对分析依据中的 diff、日志与 head SHA；检查执行轨迹和缺失证据。
4. 点击“保存评论草稿”，到“记忆与审批 → 草稿审批”提交并审核；默认仅本地保存。创建候选记忆，确认批准前检索不到，批准后可以检索；修改后需重新批准。
5. 选择 PR #19 单独分析，确认其通过的 CI 不会混入 PR #18 的失败结论。

## 接入真实仓库和模型

修改 `backend/.env`：

```dotenv
DEVFLOW_MODE=live
GITHUB_TOKEN=只读GitHubToken
LLM_BASE_URL=https://你的模型服务/v1
LLM_API_KEY=你的密钥
LLM_MODEL=支持ToolCalling和JSON输出的模型
```

重启后端和 Worker，在“连接与配置”页填写 `owner/repository`。GitHub Token 最小权限按仓库实际需求配置为 Metadata、Issues、Pull requests、Contents、Actions 的读取。模型服务必须兼容 `/chat/completions`、`tools` 和 `response_format={"type":"json_object"}`。只在本地文件保存密钥，`.env` 已加入忽略列表。

DeepSeek V4 Pro 的配置示例（[官方接口文档](https://api-docs.deepseek.com/api/create-chat-completion/)）：

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro
LLM_MAX_TOKENS=4096
LLM_REASONING_EFFORT=none
```

`none` 关闭思考模式，可改为 `low` / `high` / `max`。工具调用续轮保留模型返回的 `reasoning_content`。其他供应商不支持此参数时，删除 `LLM_REASONING_EFFORT` 配置。密钥只由后端读取，不返回给前端。

## 登录、记忆与草稿审批

默认 `AUTH_ENABLED=false` 便于单机开发，所有访问者均为未验证身份的“本地管理员”。需要分用户使用时，在本地 `backend/.env` 设置以下配置，并重启 API 与 Worker：

```dotenv
AUTH_ENABLED=true
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=自行设置至少12位的初始密码
AUTH_COOKIE_SECURE=false
GITHUB_WRITE_ENABLED=false
```

首次启用创建管理员，已存在管理员时不会按环境变量重设密码。密码使用带盐 scrypt；会话仅存 Token 哈希，12 小时过期，Cookie 为 HttpOnly、SameSite=Strict。管理员在“成员权限”创建账号并按仓库授权，停用账号会撤销会话。HTTPS 部署设置 `AUTH_COOKIE_SECURE=true`，反向代理及 `CORS_ORIGINS` 必须对应实际站点。当前是基础账号体系，尚无密码找回、管理员密码轮换界面和企业 SSO。

| 仓库角色 | 操作范围 |
| --- | --- |
| 只读成员 `viewer` | 查看授权仓库及分析，检索文档、代码和已批准记忆 |
| 编辑者 `editor` | 另可同步、发起分析、创建和修改候选记忆/草稿 |
| 审核者 `maintainer` | 另可批准/拒绝、查看审计和执行已启用的发布 |
| 管理员 | 所有仓库及账号、成员授权管理 |

记忆仅供当前仓库使用，来源运行与证据 ID 经后端校验。新分析提交时固定最近 50 条已批准记忆，工具返回相关性最高的最多 3 条；归档不改变已入队任务的证据。记忆与文档均作为检索数据，不能增加工具权限。

草稿经“编辑 → 提交审核 → 批准”后，可以复制使用。绑定 GitHub Issue/PR 时，原分析必须含该目标证据；PR 提交 SHA 在提交审核、批准和发布前重新核对。正文或目标改变会使审批失效。

确需发布评论时，需同时启用 `AUTH_ENABLED=true`、`GITHUB_WRITE_ENABLED=true`，并配置具有目标仓库写权限的 `GITHUB_TOKEN`。该入口使用 [GitHub Issue comments API](https://docs.github.com/en/rest/issues/comments#create-an-issue-comment)，也支持 PR 的普通时间线评论；fine-grained Token 需要目标仓库的 Issues 或 Pull requests 写权限。它不创建逐行 Review、不关闭 Issue、不合并 PR，也不写代码。发布前页面显示目标并由操作者确认，Agent 不持有这个写工具。

发布先持久化领取记录，再核对远程正文标记。并发请求只允许一个领取；成功后重复请求返回原评论。超时、连接中断或进程退出后进入“发布待核对”，核对入口只读取远程评论，找不到仍保持待核对。最多核对 500 条评论，标记被改动或重复时拒绝自动确认。这是应用层重复防护；GitHub API 不提供原子事务，预检与写入之间也不能锁住 PR 提交。请核对最终远程结果。本轮只通过替身测试验证发布，未实际发送 GitHub 评论。

## 项目知识与混合检索

“项目知识”页提供文档导入、BM25 检索实验台、带引用的结果和向量索引状态。相同路径重新导入会更新文档并清除旧片段；“移出知识库”暂停该手动文档的召回，重新导入同一路径即可恢复。手动文档明确标为本地来源，和 GitHub README 分开管理。

BM25 不需要额外服务。向量路线需在 `backend/.env` 配置：

```dotenv
RETRIEVAL_BACKEND=milvus
EMBEDDING_BASE_URL=https://你的Embedding服务/v1
EMBEDDING_API_KEY=单独的Embedding密钥
EMBEDDING_MODEL=你的Embedding模型
MILVUS_URI=http://127.0.0.1:19530
```

Embedding 接口需要兼容 `/embeddings` 的 `model`、`input` 和 `data[].index/embedding`。仅当服务支持自定义维度时设置 `EMBEDDING_DIMENSIONS`。生成分析的 DeepSeek 密钥不会被自动发给其他服务。

重启后端，导入文档后点击“建立向量索引”。索引按仓库和生成批次隔离；全部写入成功、文档集未变化时才激活。更改文档或模型/连接配置后，过期索引不可用于查询，需重建。相同模型的已有片段向量可复用。混合检索合并 BM25 和 COSINE 排名，采用 RRF；可选 Jina 兼容重排配置见 `backend/.env.example` 和 [Jina 官方 API](https://jina.ai/reranker/)。

检索实验台中，混合模式未就绪会明确拒绝查询。Agent 在已配置的向量/重排服务不可用时会明确记录降级提示，并以 BM25 继续；提示保存在运行记录和证据缺口中。不会把关键词结果标成向量结果。

本机已实际运行中文 CPU Embedding 和 Milvus Lite，完成索引写入及混合检索。远程 Embedding、Milvus Standalone 和重排服务仍未实机联调。Webhook 接收/后台刷新已在本地测试，真实 GitHub HTTPS 投递仍待联调；自动采集全仓库文档和向量旧批次回收待后续实现。检索得分表示排序，不是答案置信度；向量查询也可能返回无关的最近邻。

### 本地中文向量路线（v0.8）

从项目根目录安装独立环境并准备公开模型权重：

```powershell
python -m venv .vector-venv
.\.vector-venv\Scripts\python.exe -m pip install -r backend\requirements-vector.lock.txt
.\.vector-venv\Scripts\python.exe scripts\prepare-vector-model.py
```

模型为 [FastEmbed 支持的 BAAI/bge-small-zh-v1.5](https://qdrant.github.io/fastembed/examples/Supported_Models/)，输出 512 维，优化 ONNX 权重约 90 MB。准备脚本只下载公开权重，不上传仓库内容或应用密钥；模型文件与 SHA-256 清单保存在被忽略的 `backend/data/vector-models`。启动服务时校验清单并只读本地权重，离线 CPU 推理。版本标识同时绑定权重和切窗算法，避免不同模型复用旧索引。

在 `backend/.env` 设置以下字段。`EMBEDDING_MODEL` 使用准备脚本输出的完整版本标识；`EMBEDDING_API_KEY` 自行生成独立的至少 32 字符随机值，与 DeepSeek/GitHub 密钥分开。当前工作区已完成配置，无需重新生成密钥或下载模型。

```dotenv
RETRIEVAL_BACKEND=milvus
EMBEDDING_LOCAL=true
EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1
EMBEDDING_API_KEY=独立的至少32字符本地服务密钥
EMBEDDING_MODEL=bge-small-zh-v1.5-准备脚本输出的指纹前16位
EMBEDDING_DIMENSIONS=512
MILVUS_URI=http://127.0.0.1:19530
MILVUS_DEPLOYMENT=lite
```

另开两个终端，从项目根目录分别运行：

```powershell
.\scripts\start-local-embedding.ps1
```

```powershell
.\scripts\start-milvus-lite.ps1
```

然后启动或重启 API、Worker，打开“项目知识”，依次点击“检查向量连接”和“建立向量索引”，选择“BM25 + 向量 / RRF”查询。索引状态记录已保存版本，连接检查单独验证服务是否在线。Milvus 数据保存在 `backend/data/milvus-lite`，服务重启继续使用同一目录。无需 Docker。

[官方 Milvus Lite 3.2.1 包](https://pypi.org/project/milvus-lite/)支持本轮使用的 Windows Python 环境。本地脚本固定监听回环地址，并以一个 RPC 工作线程串行处理请求，避免 Lite 的并发写入限制。该服务没有身份认证或 TLS，仅用于本机开发；团队部署需另行验证 Standalone、权限和生产容量。本路线依赖独立 Python 环境，当前 Compose 镜像不包含这些组件。

Lite 客户端关闭空闲保活 Ping，避免 PyMilvus 默认短间隔保活触发服务端 `too_many_pings` 断连；实际连接检查仍通过只读 RPC 执行。说明见 [gRPC 保活策略](https://grpc.io/docs/guides/keepalive/)。

输入最多 32 条、每条 6000 字符，长片段按 350 字符窗口、70 字符重叠，取窗口向量均值再归一化；这是保留尾部内容的近似表示。周报的每个召回片段都带历史摘要提示，不能代替最新源码和已批准记忆。索引、模型或文档版本改变后应重新建立索引。

## 代码工作区与任务规划

在“代码工作区”选择 GitHub 默认分支并点击“同步远程代码”，或在本地开发环境选择“本地项目源码”并点击“创建源码快照”。本地导入只读取当前 DevFlow 项目，不接受任意磁盘路径，也不推送 GitHub。空远程仓库无法提供代码证据；可先用本地快照分析项目实现。

文件列表和检索结果附路径、行号与完整提交版本，支持分段浏览及“协作分析此文件”。源码检索使用 BM25、标识符拆词、路径及 Python 定义匹配；例如 `renew lease` 可以匹配 `renew_lease` 和 `renewLease`。Python 使用 [AST](https://docs.python.org/3/library/ast.html) 识别函数、类、方法、嵌套作用域和装饰器，解析不执行源码。其他语言和解析失败的 Python 按行切分，页面明确标注回退。每段最多 80 行、6000 字符，长定义分段并标注部分覆盖，超长单行记录为遗漏。得分用于排序，不代表正确率；尚未实现语义源码向量检索。Agent 工具和源码页面共用检索实现。

工作台“协作检查”会先展示模型任务计划，再显示各任务状态、依赖与证据缺口。分析在开始时固定源码版本；同步新版本后，历史分析仍引用旧提交。同步只读取 Git 对象，不运行仓库测试、构建、hook 或其他脚本。

默认总任务预算 6 个、并发 2 个、单个专用任务超时 120 秒；启用补查时首轮最多 4 个任务，剩余 2 个用于一次补查。配置见 `WORKFLOW_MAX_TASKS`、`AGENT_CONCURRENCY`、`AGENT_TIMEOUT_SECONDS`、`MAX_REPLANS`。计划仅能安排注册的只读分析，不执行仓库脚本；CI 关联 PR 时重新读取当前 head 检查，拒绝使用其他提交的 run。

工作区排除 `.env`、密钥文件、依赖目录、符号链接、子模块和超限文件，并对配置中的密钥和常见 Token 格式脱敏。这些过滤不保证识别所有秘密。默认读取最多 500 个文件，单文件 256 KB，检索总量 8 MB；一次读取最多 400 行。Git 对象保存在被忽略的 `backend/data/code`，尚未实现旧对象回收和 PR 分支自动获取。

## 停止、中断和恢复

工作台点击“停止”会通过数据库请求 Worker 取消执行并保留恢复点，已排队任务立即取消。在“运行记录”重新打开未完成的协作检查，点击“从断点继续”，重新进入后台队列。API 重启不修改仍有有效租约的后台运行；Worker 心跳过期后将运行标为 interrupted。恢复沿用同一运行 ID，追加事件，保留原任务的证据和依赖结果。源码缓存缺失、已分析 PR 的 head SHA 改变、运行模式或模型配置改变时会拒绝复用；排队恢复在提交和真正执行时分别检查 PR 版本。

恢复基于应用层的 SQL 任务检查点，**不是 LangGraph BaseCheckpointSaver**。已持久化的 completed、partial、failed、skipped 任务保留，不重复执行；没有持久化终态的任务可重试。进行中的单次模型请求、工具调用或任务内部状态不会续接，未保存的响应可能需再次请求。原运行已耗用的补查机会不会因恢复而重置。

每个恢复点保存原仓库快照、完整源码 SHA、文档片段版本、任务计划、结果和预算，不保存连接密钥。文档库后来更新或归档时，原运行仍可按旧片段做 BM25 检索，并明确标注版本；过期向量索引不用于该旧快照。最终结论说明它沿用了原版本，不代表当前全部 GitHub 状态。

默认最多恢复 3 次，可用 `MAX_RESUME_ATTEMPTS` 调整（0 禁用，上限 5）。原任务预算不增加，同一运行通过数据库状态条件更新和执行令牌防止重复启动。检查点大小限制为 16 MB。仅 v0.4 及以后新建的“协作检查”保存恢复点；旧记录与其他分析任务可回看，需重新发起分析。

## 后台执行与连接边界

页面使用 `POST /api/chat/runs` 入队，之后订阅当前仓库运行的 `GET .../events?after=N`。SSE 从持久化事件重放，页面用序号去重；结束事件丢失时可重新连接获取。点击“暂离工作台”只关闭订阅，从运行记录重新打开运行即可继续观看。历史列表自动刷新；页面刷新后可在该列表找到排队或运行中的任务。

队列与 Worker 注册信息存入 SQL，新安装不需要 Redis。默认每个 Worker 同时执行 2 个运行（`WORKER_CONCURRENCY`），每次轮询 0.5 秒（`QUEUE_POLL_SECONDS`），执行租约为 30 秒（`JOB_LEASE_SECONDS`）。API 和 Worker 都可清理过期租约；条件更新和执行令牌限制旧 Worker 的事件、检查点及结果写入。进程强制退出后不会自动重复调用模型，协作检查可手动恢复，其他任务需重新发起。排队与运行中的任务总量上限为 100；这是开发环境容量保护，不是大规模队列或公平调度系统。

目前本机验证 SQLite，PostgreSQL 和 Docker Compose 尚未实机运行；Compose 加入独立 Worker 并共享数据库与代码数据卷。基础仓库角色、批准记忆和 GitHub 写回审批已实现，但默认本地模式未启用身份验证。原 `POST /api/chat/stream` 与非后台 `/resume` 保留用于旧客户端和回归测试，仍采用断连取消行为；新版页面使用后台接口。

实现参考：[Python asyncio 任务与取消](https://docs.python.org/3.12/library/asyncio-task.html)、[SQLAlchemy 条件 UPDATE](https://docs.sqlalchemy.org/en/20/orm/queryguide/dml.html)、[FastAPI StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/)。

前端通过 Next.js 同源代理访问后端。后端端口变更时，在 `frontend/.env.local` 中设置 `DEVFLOW_API_URL` 并重启前端。

这是本地开发版本，登录与仓库权限可选启用；尚未实现迁移管理和多机器共享代码缓存。v0.6 仅通过新增表保留旧记录，不修改原表字段。页面与独立 Worker 通过 SQL 队列解耦，支持事件重放与断线重连；Worker 中断后的任务恢复需要用户手动触发。当前只监听回环地址，团队部署仍需 HTTPS、身份配置与生产验证。

## Docker / PostgreSQL

本机尚未安装 Docker，因此以下配置尚未通过容器运行验证。安装 Docker Desktop 后，从根目录运行：

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

此配置启动 PostgreSQL、后端和前端，端口只绑定 `127.0.0.1`。本地 Python 与容器运行是两条启动路线，避免同时占用 3000/8000 端口。数据库通过命名 volume 保存；停止服务使用 `docker compose down`，无需删除数据卷。

`infra/compose.milvus.yaml` 是 Milvus 3.0.2 官方独立部署配置，`scripts/prepare-milvus.ps1` 可重新下载。安装 Docker 后可运行 `docker compose -f infra/compose.milvus.yaml up -d`，然后按上面的步骤配置 Embedding 并建立索引。Python SDK 固定为同版本。参考[Milvus 官方部署说明](https://milvus.io/docs/install_standalone-docker-compose.md)。使用后端容器连接宿主机上的 Milvus 时，`MILVUS_URI` 使用 `http://host.docker.internal:19530`。

## 有限源码事实核对（v0.12）

工作台在建议之前展示核对范围、与声明一致的数值、冲突及未验证项。检查器只解析有合法源码证据 ID、固定 SHA、路径及完整行号范围的 Python 片段，不运行源码、不调用额外模型。支持模块中唯一的直接全大写数值声明，以及结构明确的 BM25 公式中 k1、b；局部变量仅用于解析公式中的唯一直接字面量绑定。算术表达式、解析失败、部分函数的局部绑定、不同文件/作用域/版本混用，均不当作唯一来源。

回答支持的语法为 `NAME=数值`、`NAME:数值`、`NAME为数值` 等，名称限全大写常量、k1/k_1/b。否定、假设与修改建议保留未验证；无法识别的数值写法和其余自然语言解释仍需人工复查。finding 只核对它实际引用的证据，其余字段使用本次提供的来源；“一致”只表示该数值与引用声明一致，不表示回答整体正确。

v0.12 另从同一评分公式提取分子乘数、长度常数项与参数绑定，支持有限关系句式，例如“分子乘数2.2等于k1+1”“分子中的2.2不是k1+1”“k1+1=2.2”“0.25=1-b”。连等式中只核对支持的关系对，不把 `k1+1=1.2+1` 或 `1-b=1-0.75` 截成数值声明。结果按数值声明和公式关系分别计数，未识别的语法不计入。代码直接写了字面量与数学上是否等于参数表达式分别处理；建议、假设、引述、源码写法说明保留未验证。检查器使用引用版本的参数计算预期关系，不硬编码 k1=1.2，也不执行表达式或源码。数值很小或很大时采用有上限的精确十进制运算。

这只是两个表达式的有限语法检查，不是通用语义验证。没有识别完整调用图、运行值、全局可变状态或所有否定句，也没有用另一模型担保整段回答。公式数值与所描述数学关系冲突时仍可留存来源事实；不能因来源中用了字面量而否认同一数值关系。

冲突标题、摘要、finding、下一步或待确认项会移出主要正文，finding 的标题或详情冲突时整项移出，保留原分析和每条原表述。协作子任务的冲突会传到最终汇总，不能因模型重写而被忽略。存在冲突的分析暂停自动生成新的评论草稿；新周报只使用核对后的摘要，并标明冲突。人工编辑的既有草稿、批准记忆及旧周报保持原样。

历史运行的“核对源码事实”使用当时保存的证据生成只读预览，不覆盖原结果、事件或检查点，也不查询最新 GitHub 或发起模型请求。新分析自动执行此有限核对，并保存检查器、输入与来源指纹。源码数值核对回归是人工行为测试，不是通用事实验证或模型质量评分。

## 固定评测、用量与周报

“评测与交付”页包含四个入口。编辑者可以运行评测和生成周报，只读成员可以查看，周报存入知识库需要审核者。

当前批次为 `core-v4`：`backend/evals/core-v1.json` 的 8 条样例、`backend/evals/code-v1.json` 的 10 条源码问题，以及 `backend/evals/facts-v2.json` 的 42 条核对回归（16 条数值 + 26 条有限关系与解析边界），共 60 条。原 core-v1/core-v2/core-v3 历史批次保持原数据与评分结果。文档比较标题关键词与 BM25 的 Precision、Recall、MRR@2；3 条正例进入均值，1 条无匹配单独检查。源码比较冻结的 v0.9 行关键词策略与当前 BM25/标识符/Python 定义策略，报告 Top 1 命中、Recall@5、MRR@5；8 条正例以路径及完整标注行段评分，另外检查无匹配及目录隔离。每批保存数据、评分器与实现指纹。分析运行确定性演示 Agent，检查引用、目标和规定输出。**固定样例通过不代表真实大模型准确率**；尚未完成真实仓库标注集、Prompt A/B、通用语义冲突或模型回答忠实度评测。

## 真实回答人工评审（v0.13）

打开“评测与交付 → 回答评审”，选择一条已完成分析，先展开当时的证据原文，再逐项标注摘要、发现、建议和待确认项。每项可选有证据支持、与证据冲突、证据不足、不作事实判断；标注必须填写理由，支持和冲突还必须选取至少一项本回答中的证据。可以只标注部分字段，未标注项不计为正确。统计仅覆盖最近 30 次已完成分析，按最新且仍匹配回答版本的评审计算，不显示模型准确率。

保存须有 maintainer/admin 权限；viewer/editor 可以读取和导出。评审只追加版本，不改原回答或原执行事件，也不批准草稿、记忆或发布评论。自动移出的冲突段落从保存的原回答恢复供评审，有限自动核对单独展示。回答或证据变化后旧标注过期，历史版本仍可按保存时的原文读取。页面历史列表最多显示 20 个版本；单次评审最多 100 个字段、128 项证据、40 万字符。

导出 JSON 包含问题、原回答字段、来源证据、回答指纹、当前与历史保存快照、最新标注和保存时的有限自动核对，可作为后续人工整理标注集的输入。未经过人工审核的导出不是 gold dataset；评审工具没有自动语义裁判或代表性抽样。API 使用 `GET /api/repositories/{id}/answer-reviews/status`、`GET /answer-reviews/export` 及 `GET/POST /runs/{run_id}/answer-reviews`（后两项同一仓库前缀）。POST 必须提交当前 `snapshot_hash` 和 `expected_version`，并发或过期保存返回 409，重复同一提交复用记录。

### Prompt 版本与回答对照（v0.14）

真实模式下，在输入框选择“基线 v1”或“证据优先 v2”。前者保留此前的分析指令，后者增加逐字段来源检查和事实/建议区分。选择只作用于结构化分析；Planner 与工具决策不变，协作中的专用分析和汇总使用同一版本。服务端只接受这两个代码维护的 ID，不允许传入任意 Prompt。演示模式仍使用规则，不构成模型对照。

版本、模板和模型参数在提交时固定，Worker 排队执行及恢复前核对；模板或模型参数变化时停止并提示重新分析。新模型结果保存实际系统指令、角色、模型参数和问题/完整模型输入/来源指纹，不增加完整用户请求正文或密钥存储。旧任务缺少绑定时可沿用当前基线继续，但不会为旧回答补造历史版本。

打开“评测与交付 → 回答对照”，选两条已完成记录，并排查看原回答、固定证据、最新有效人工标注与真实用量，支持 JSON 导出。此页面不调用模型。只有单次 live 源码/知识分析在问题、角色、完整模型输入（含缺口与提取事实）、全部来源、模型服务/参数和分析协议一致，且两个 Prompt 版本不同时，才显示满足对照条件。ChatAgent、多 Agent、演示和降级结果只可作历史查看；缺少版本、证据改变或来源记录无法核对时明确列出原因。

同源条件不判断胜者，不计算模型准确率或胜率。人工标注未完成的字段仍是未标注，既有实际历史记录也没有 v0.14 版本信息；代表性真实 Prompt 样本和人工质量评估仍待建设。v0.14 执行验证使用受控 HTTP 响应和临时数据库，没有新增实际模型请求。API 为仓库前缀下 `GET /analysis-prompts`、`GET /answer-comparisons/runs`、`GET /answer-comparisons?left={run_id}&right={run_id}` 和 `GET /answer-comparisons/export`（同样的两项查询参数）；仓库只读角色可读，MCP 白名单不扩展。

### 一次提交两版（v0.15）

“评测与交付 → 回答对照”上方新增两版提交表单，editor/maintainer/admin 可在 live 模式选择源码或知识任务。填写同一个问题后提交，将固定同一份仓库快照、源码 SHA、文档片段、已批准记忆和模型参数，在一个 SQL 事务中创建 baseline-v1 与 evidence-first-v2 的两次后台分析。每次使用新会话、空历史与相同目标，不纳入 Planner 或工具自主决策。创建后不代表输入已经验证相同，完成时仍由对照接口核对实际完整模型输入。

提交会使用两次真实模型请求，费用按供应商实际用量记录，缺少单价时保持未知。网络请求重试使用原 `request_id`，相同问题/任务/提交者复用已保存记录，改变内容或提交者返回冲突；并发重复也只创建一组。两次任务只在事务提交后对 Worker 可见，队列不足时整组回滚，不出现只入队一版的情况。界面保留本次未确认请求的编号，失败重试沿用它；重新加载页面后应先查看记录，重新提交属于新一组分析。

关闭页面不取消执行。界面显示两侧排队、执行、完成、失败或停止状态，支持停止当前组；已完成结果不回写。失败、中断和停止不会自动重试模型，不制造可比较的答案。最新 20 组记录可查看，读取、停止或导出本身不会新发模型请求；已完成的旧组可按保存的运行 ID 读取，超过最近 30 次候选窗口也不会跳转到其他回答。viewer 可读，MCP 权限不扩展。API 在仓库前缀下使用 `GET/POST /prompt-experiments`、`GET /prompt-experiments/{id}`、`POST /prompt-experiments/{id}/stop`，提交体只接受 `request_id`、`question` 与 `task`（code/knowledge）。

v0.15 已完成用户批准后的真实 DeepSeek V4 Pro 对照联调：baseline-v1 与 evidence-first-v2 各调用一次，使用同一个问题和 `backend/app/code_search.py` 固定源码，两次实际完整输入均与批准预览一致。两条回答、引用、版本、用量和 JSON 导出已保存，可在本页选择完成记录查看。有限数值/关系检查未发现冲突，证据优先版有 4 处建议或源码写法表述保持未判定；这一组只验证执行与记录链路，不判断哪版质量更高。详细调用、授权经过及验收限制见 [验证记录](docs/verification.md)。

### 固定评估样本集（v0.16）

在“评测与交付 → 评估样本集”填写名称和选择说明，选取最近 20 组中的已完成真实对照，保存为固定样本集。editor/maintainer/admin 可创建，viewer 只读；每集最多 20 组、12 MB。服务端逐组核对实际同源条件，未完成、旧版本未知或输入不同的组不能加入。保存与读取均使用已有回答，不调用模型，也不自动添加人工标注。

样本集保留保存时的原回答、固定证据、Prompt/模型参数和用量，有内容指纹且不可覆盖。即使原运行退出最近 30 次候选范围，仍可从样本集打开对应回答评审。人工评审继续使用原版本接口，样本集统计当前有效标注的字段覆盖和四类标签数量，按 Prompt、模型参数、角色和协议分别列示；不计算胜者、胜率或模型准确率。回答、证据或版本与固定样本不一致的组退出当前统计，历史原文仍可导出。

相同提交编号、内容和提交者重试返回原集；更改内容或提交者时拒绝，并发重试只保存一份。页面失败后保留本页编号，重载页面后应先查看已有列表。导出包含固定原文、当前有效评审、过期/变化原因及覆盖统计。不同输入数量只用于提示重复样本，单一问题或全部完成标注不代表评估具有代表性。

Ragas 与应用依赖分开安装，在项目根目录运行：

```powershell
python -m venv .eval-venv
.\.eval-venv\Scripts\python.exe -m pip install -r backend\requirements-evaluation.lock.txt
.\.eval-venv\Scripts\python.exe -m pip check
```

然后点击“运行本地 Ragas”。固定使用 Ragas 0.4.3 的 `NonLLMContextRecall` 和 `NonLLMContextPrecisionWithReference`，通过字符串相似度、默认阈值 0.5 比较 6 条正例样本的召回片段与参考片段。它不调用模型，不测答案忠实度。子进程仅接收项目自带的固定文本，不传用户仓库数据；去除密钥相关环境变量，并关闭 Ragas 遥测和 Hugging Face 在线访问。记录可以导出 JSON，重复请求复用同一批结果。当前自动查找宿主机根目录的 `.eval-venv`；容器镜像未包含该可选环境，不能把本机验证当作容器联调。

耗时统计只记录 v0.7 新执行的阶段，排队时间不计入执行耗时；每次恢复单独记录，再汇总到原运行。历史未测量的请求、进程中断后未返回的用量均标为未知。模型请求记录只保存模型名称、时间、状态、供应商返回的输入/输出与缓存 Token，以及当时的单价，不保存请求正文和密钥。未记录供应商用量时不猜测为零。页面展示最近 30 次运行。

费用默认未知。若要估算，在本地 `.env` 按供应商当前价格填写 `LLM_INPUT_PRICE_PER_MILLION`、`LLM_OUTPUT_PRICE_PER_MILLION` 和 `LLM_CACHED_INPUT_PRICE_PER_MILLION`，单位均为每百万 Token，并设置 `LLM_PRICE_CURRENCY`（默认 CNY）。输入单价表示缓存未命中的部分；有缓存命中但缺少缓存单价时，整次费用保持未知。每次请求固定当时的配置，之后修改单价不会重算历史账目。此值是估算，不是供应商账单；项目不自动抓取价格，也不将未知填成 0。配置修改后重启 API 与 Worker。

周报按北京时间周一至周日，依据运行创建时间汇总当前模式的已完成分析，最多 100 条。它是**本地已保存分析摘要，不是 GitHub 全量活动周报**，保存运行 ID、证据和待确认项。相同内容重复生成返回原记录；确认正文后点击“确认并存入知识库”，生成独立的本地 Markdown 文档并留下审计。没有外部消息发送或定时发布。库中内容仍作为证据数据，不能改变 Agent 权限。

## MCP 只读扩展

使用官方 [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) 的固定 1.30.0 版本，提供 `repository_snapshot`、`search_knowledge`、`search_approved_memories`、`saved_run`、`read_code`、`weekly_reports`。仅使用 stdio；服务端通过固定回环地址访问 FastAPI，禁止自定义远程地址、代理和重定向。搜索使用 BM25，记忆只返回已批准版本，源码沿用路径与读取行数限制。

在本地 `backend/.env` 设置独立随机的 `MCP_ACCESS_TOKEN`（至少 32 个字符），以及 `MCP_REPOSITORY_IDS`（逗号分隔的本地仓库 ID，可从 MCP 页面复制）。不要复用模型或 GitHub 密钥。重启 API 后，让支持 stdio 的客户端启动 `scripts/start-mcp.ps1`；它使用项目虚拟环境并切到 backend 工作目录。API 默认端口 8000，需要调整时仅在 MCP 启动环境设置 `DEVFLOW_MCP_API_PORT`。API 必须单独运行。直接在终端启动会等待客户端协议输入，这是正常行为。

该 Token 的身份固定为只读，仅能请求授权仓库的白名单接口。错误 Token 不会回退到本地管理员。候选记忆、草稿、账号管理、创建分析、审批及发布均被拒绝；这些工具不注册到项目 Agent 的写操作中。默认 Token 和白名单均为空。本轮协议联调使用独立临时数据库与测试 Token；没有向实际仓库开启外部客户端访问。

## 验证

```powershell
# 根目录
.\.venv\Scripts\python.exe -m pytest backend\tests -q

# frontend 目录
npm run typecheck
npm run build
```

详细测试结果和限制见 [验证记录](docs/verification.md)，下一阶段的设计与验收见 [实现路线](docs/implementation-plan.md)。

## 目录

```text
backend/app/         FastAPI、数据模型、GitHub 工具、模型接入和 LangGraph
backend/skills/      四个服务端固定的声明式任务流程，不执行脚本
backend/tests/       关键行为与约束测试
frontend/src/        Next.js 看板、工作台、知识、历史、配置
scripts/             Windows 启动脚本
docs/                实现路线、验证记录
compose.yaml         PostgreSQL + 前后端本地部署
```

接下来的主要工作：真实标注集与模型回答质量/Prompt 比较、语义冲突评测、任务 Skill 的代表性真实验证及扩展；补齐 Standalone/重排、Webhook HTTPS 实际投递、私有仓库写回与容器部署联调。
