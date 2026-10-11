# 自动检查

`.github/workflows/checks.yml` 在 main 更新、面向 main 的 PR 和手动运行时执行。后端用 Python 3.12 与锁定依赖运行后端、云端启动及部署核验脚本的隔离回归；前端用 Node 24 安装锁定依赖，执行生产构建、TypeScript、移动离线策略和 Android HTTPS 地址策略检查。每个 job 最长 15 分钟，同一分支的新提交取消过期检查。

PR 检查固定检出 PR head SHA，主分支检查固定检出触发提交。GitHub Token 只有 Contents read，检出后不保留 Git 凭据；不使用仓库模型密钥、生产密码或 Railway Token。后端测试使用演示模式与临时 SQLite，默认跳过需要本机 Embedding/Milvus 的向量实机检查；该跳过保持在日志中，不计为通过。

查看 [Actions](https://github.com/Mysiya/DevFlow/actions)。在 DevFlow 的“连接与配置”同步看板，再选择实际 PR 编号或 Actions run ID 分析。CI 成功只说明当前流水线中的检查通过，不代表安卓实机、全部发布条件或模型回答质量已验收。

`diagnosis-drill.yml` 只在手动触发时运行，名称明确标记诊断演练。唯一 job 输出 `DEVFLOW_CI_DIAGNOSIS_DRILL` 并以预定的 42 退出，用来验收真实 GitHub 失败日志读取与模型证据引用；它没有运行应用测试，不能将它报告成业务测试缺陷。该演练失败与正常 DevFlow checks 的检查结果分别记录。PR 协作分析仍只关联其当前 head SHA 的 run，不用其他提交的演练替代当前 CI。

CI 证据固定读取观察到的 `run_attempt`，保留 job ID、链接及各步骤状态。签名日志下载失败时保留这些证据并记录缺口，不使整次查询失败，也不回传含签名的异常 URL。最多读取当前轮次 100 个 job 和前三个失败 job 日志，每份最多 512 KB，合并最多 25000 字符；保存给模型的 CI 证据正文最多 15000 字符，各层截断均明确提示。GitHub 凭据只发送给官方 API，下载签名日志的独立客户端不携带该凭据。正式服务未设置 GitHub Token 时，可查询公开 job 和步骤；若平台拒绝匿名下载日志，结果必须保留日志缺口，不能报告已确认根因。

Railway 正式服务连接 Mysiya/DevFlow 的 main。源码连接、GitHub App 仓库授权、Auto Deploy 和 Wait for CI 分别核对：`serviceInstanceAutoDeployStatus` 的 enabled 表示是否启用，canEnable 与 reason 表示授权条件；目标服务的 `deploymentTriggers` 还须匹配仓库、环境、main 分支和 `checkSuites=true`。账号控制台中勾选 Wait for CI 后若出现“Apply change”，须先审阅并应用待变更，不能把勾选状态当成已保存。参见 [Railway 自动部署要求](https://docs.railway.com/deployments/github-autodeploys)。

2026-10-11 排查时，服务级状态曾返回 `enabled=false`、`canEnable=false`、`reason=NO_INSTALLATION`，项目令牌启用请求返回 Bad Access。用户安装[官方 Railway App](https://github.com/apps/railway-app/installations/new)并授权 DevFlow 后，canEnable 恢复为 true；通过已登录控制台启用自动部署，并审阅、应用仅将 Check Suites 从 false 改为 true 的配置。验收另用实际文件改动的新提交，观察 CI 期间的 WAITING、通过后的部署记录，再核对运行完整 SHA。配置变更触发的构建单独记录，不计为推送自动部署证明；失败或未观察到的环节保留为未通过。

管理员登录后可读取 `GET /api/admin/deployment` 的 `git_commit_sha` 与 `deployment_id`，只返回格式合法的平台提交 SHA 和部署 ID，缺失或异常均为 null；它们是平台提供的元数据，不是重新计算镜像源码摘要。启用登录的服务器上，匿名访问返回 401，普通用户返回 403，响应禁止缓存。`scripts/verify-deployment.py --url HTTPS服务地址 --expected-sha 完整提交SHA` 使用进程环境中的 `DEVFLOW_ADMIN_USERNAME`、`DEVFLOW_ADMIN_PASSWORD` 登录，只有服务报告的完整 SHA 相同且健康检查正常才输出 verified。账号密码不要写进命令参数或提交仓库；脚本不调用模型，退出前撤销自己的登录会话。参考 [Railway 提供的变量](https://docs.railway.com/variables/reference)。

完整读取失败日志时，创建仅选择 DevFlow 的细粒度 Token，在 Repository permissions 中设置 Actions 读取。可使用[预填权限页面](https://github.com/settings/personal-access-tokens/new?name=DevFlow%20CI%20read&target_name=Mysiya&expires_in=30&actions=read)，然后手动选择仓库；该链接只预填权限和期限，不自动授权仓库。将生成的值写入本机 `backend/.env` 的 `GITHUB_TOKEN` 并保存，正式服务使用独立密封变量。不会将电脑用于上传源码的 Git 登录凭据复制到服务器，也不启用应用 GitHub 写回。

真实链路记录：PR #1 的 head 为 `c32c775f7b927f3fbdb6250cef7095f2d8adc00e`；[PR checks](https://github.com/Mysiya/DevFlow/actions/runs/38034611470) 与[主分支 checks](https://github.com/Mysiya/DevFlow/actions/runs/38034820930) 均成功。云端 PR 审查和发布前协作检查已完成；协作的 CI 节点实际选中 run 38034820930，与该 head 匹配，没有关联其他提交的失败演练。它验证了模型执行和证据关联，不代表全部审查结论正确或已经满足发布条件。

正式服务现已配置用户另行提供的细粒度 Token，存为密封 `GITHUB_TOKEN`，应用写回保持关闭。云端 CI 排障已读到 run 38034194507 的实际失败日志、演练标记与 exit 42；模型明确解释为预期诊断演练，没有将其报告成应用测试缺陷。网页或 APK 中选择“CI 排障”，填写完整 run ID；发布前协作检查则填写 PR 编号，由服务端核对其 head 的 CI。

参考：[GitHub 工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)、[Actions job 日志与固定重跑轮次](https://docs.github.com/en/rest/actions/workflow-jobs)。
