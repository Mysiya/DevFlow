# 自动检查

`.github/workflows/checks.yml` 在 main 更新、面向 main 的 PR 和手动运行时执行。后端用 Python 3.12 与锁定依赖运行隔离回归；前端用 Node 24 安装锁定依赖，执行生产构建、TypeScript、移动离线策略和 Android HTTPS 地址策略检查。每个 job 最长 15 分钟，同一分支的新提交取消过期检查。

PR 检查固定检出 PR head SHA，主分支检查固定检出触发提交。GitHub Token 只有 Contents read，检出后不保留 Git 凭据；不使用仓库模型密钥、生产密码或 Railway Token。后端测试使用演示模式与临时 SQLite，默认跳过需要本机 Embedding/Milvus 的向量实机检查；该跳过保持在日志中，不计为通过。

查看 [Actions](https://github.com/Mysiya/DevFlow/actions)。在 DevFlow 的“连接与配置”同步看板，再选择实际 PR 编号或 Actions run ID 分析。CI 成功只说明当前流水线中的检查通过，不代表安卓实机、全部发布条件或模型回答质量已验收。

`diagnosis-drill.yml` 只在手动触发时运行，名称明确标记诊断演练。唯一 job 输出 `DEVFLOW_CI_DIAGNOSIS_DRILL` 并以预定的 42 退出，用来验收真实 GitHub 失败日志读取与模型证据引用；它没有运行应用测试，不能将它报告成业务测试缺陷。该演练失败与正常 DevFlow checks 的检查结果分别记录。PR 协作分析仍只关联其当前 head SHA 的 run，不用其他提交的演练替代当前 CI。

CI 证据固定读取观察到的 `run_attempt`，保留 job ID、链接及各步骤状态。签名日志下载失败时保留这些证据并记录缺口，不使整次查询失败，也不回传含签名的异常 URL。最多读取当前轮次 100 个 job 和前三个失败 job 日志，每份最多 512 KB，合并最多 25000 字符；保存给模型的 CI 证据正文最多 15000 字符，各层截断均明确提示。GitHub 凭据只发送给官方 API，下载签名日志的独立客户端不携带该凭据。正式服务未设置 GitHub Token 时，可查询公开 job 和步骤；若平台拒绝匿名下载日志，结果必须保留日志缺口，不能报告已确认根因。

Railway 正式服务已连接 Mysiya/DevFlow 的 main，并成功从 GitHub 构建上线 `c32c775`；推送后自动触发仍在核验，不能将源码连接成功写成自动更新已通过。当前未设置“等待 CI”，应先通过本机检查并核对 Actions，再用于正式部署；不要把服务部署成功当成 CI 已通过。后续需要平台审批或分支保护时，应单独配置并核对账号权限。

完整读取失败日志时，创建仅选择 DevFlow 的细粒度 Token，在 Repository permissions 中设置 Actions 读取。可使用[预填权限页面](https://github.com/settings/personal-access-tokens/new?name=DevFlow%20CI%20read&target_name=Mysiya&expires_in=30&actions=read)，然后手动选择仓库；该链接只预填权限和期限，不自动授权仓库。将生成的值写入本机 `backend/.env` 的 `GITHUB_TOKEN` 并保存，正式服务使用独立密封变量。不会将电脑用于上传源码的 Git 登录凭据复制到服务器，也不启用应用 GitHub 写回。

真实链路记录：PR #1 的 head 为 `c32c775f7b927f3fbdb6250cef7095f2d8adc00e`；[PR checks](https://github.com/Mysiya/DevFlow/actions/runs/38034611470) 与[主分支 checks](https://github.com/Mysiya/DevFlow/actions/runs/38034820930) 均成功。云端 PR 审查和发布前协作检查已完成；协作的 CI 节点实际选中 run 38034820930，与该 head 匹配，没有关联其他提交的失败演练。它验证了模型执行和证据关联，不代表全部审查结论正确或已经满足发布条件。

正式服务现已配置用户另行提供的细粒度 Token，存为密封 `GITHUB_TOKEN`，应用写回保持关闭。云端 CI 排障已读到 run 38034194507 的实际失败日志、演练标记与 exit 42；模型明确解释为预期诊断演练，没有将其报告成应用测试缺陷。网页或 APK 中选择“CI 排障”，填写完整 run ID；发布前协作检查则填写 PR 编号，由服务端核对其 head 的 CI。

参考：[GitHub 工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)、[Actions job 日志与固定重跑轮次](https://docs.github.com/en/rest/actions/workflow-jobs)。
