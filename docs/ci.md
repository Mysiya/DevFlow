# 自动检查

`.github/workflows/checks.yml` 在 main 更新、面向 main 的 PR 和手动运行时执行。后端用 Python 3.12 与锁定依赖运行隔离回归；前端用 Node 24 安装锁定依赖，执行生产构建、TypeScript、移动离线策略和 Android HTTPS 地址策略检查。每个 job 最长 15 分钟，同一分支的新提交取消过期检查。

PR 检查固定检出 PR head SHA，主分支检查固定检出触发提交。GitHub Token 只有 Contents read，检出后不保留 Git 凭据；不使用仓库模型密钥、生产密码或 Railway Token。后端测试使用演示模式与临时 SQLite，默认跳过需要本机 Embedding/Milvus 的向量实机检查；该跳过保持在日志中，不计为通过。

查看 [Actions](https://github.com/Mysiya/DevFlow/actions)。在 DevFlow 的“连接与配置”同步看板，再选择实际 PR 编号或 Actions run ID 分析。CI 成功只说明当前流水线中的检查通过，不代表安卓实机、全部发布条件或模型回答质量已验收。

Railway 正式服务已连接 Mysiya/DevFlow 的 main，后续推送触发自动构建。该连接本身没有设置“等待 CI”，当前代码应先通过本机检查并核对 Actions，再用于正式部署；不要把服务部署成功当成 CI 已通过。后续需要平台审批或分支保护时，应单独配置并核对账号权限。

参考：[GitHub 工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)、[Actions job 日志与固定重跑轮次](https://docs.github.com/en/rest/actions/workflow-jobs)。
