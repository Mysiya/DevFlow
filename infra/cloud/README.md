# 单服务云部署

根目录 Dockerfile 将 Next.js、回环 API 和独立 SQL Worker 放在同一台机器，由 run_service.py 管理启动、退出与信号。这保留三个独立进程和共享代码工作区，适合个人部署；任一进程退出时整个服务退出，交由云平台重启。公网只开放 PORT（默认 8080）。

使用当前 GitHub 仓库根目录构建，挂载持久目录 `/app/data`。默认 SQLite 文件与固定源码对象均存放在该目录，不把临时容器文件系统作为持久存储。Railway 卷权限应按平台说明设置运行 UID；若使用 PostgreSQL，可另行填写 DATABASE_URL，但代码工作区仍需持久目录。旧电脑的记录不会自动迁入新服务。

运行时必须配置：

```dotenv
CORS_ORIGINS=https://实际分配的服务域名
AUTH_ENABLED=true
AUTH_COOKIE_SECURE=true
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=自行生成的独立长密码
DEVFLOW_MODE=live
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro
LLM_MAX_TOKENS=4096
LLM_REASONING_EFFORT=none
LLM_API_KEY=仅填在云平台的服务端变量中
```

启动器强制登录、Secure Cookie、HTTPS Origin，并保持应用自动 GitHub 发布关闭。源码上传使用电脑已有的 Git 登录；该 Git 凭据不会复制到运行服务器。公共仓库同步通常无需 Token，私有仓库应单独配置只读 Token。

平台分配地址且 `/api/auth/session` 返回 auth_enabled=true 后，用该根地址执行 scripts/build-android.ps1 的 -ServerUrl 参数。APK 首次打开直接连接服务，登录后进入工作台，无需填写地址；连接设置仍可用于换服务器。

Railway Cloud Agent VM 用于开发预览；正式流量使用常规服务。平台账户、可用余额和存储需持续维护。

参考：[Dockerfile 构建](https://docs.railway.com/builds/dockerfiles)、[HTTPS 地址](https://docs.railway.com/networking/public-networking)、[持久卷](https://docs.railway.com/volumes)、[临时 VM](https://railway.com/free-vm)。

## 当前正式服务配置

已在用户领取的 `pure-communication` 项目中创建常规服务 `devflow`，分配地址 https://devflow-production-ef63.up.railway.app，挂载 `/app/data` 持久卷。使用根 Dockerfile 构建，健康检查为 `/api/auth/session`，单副本、失败最多重启 10 次，关闭应用休眠。按 [卷权限说明](https://docs.railway.com/volumes)设置 `RAILWAY_RUN_UID=0`，避免默认非 root 用户无法写入挂载目录。模型密钥和管理员密码使用平台密封变量，不随源码上传。

当前项目令牌可配置服务、卷、域名和变量，GitHub source connect 返回 Unauthorized；使用官方 `railway up --project ... --environment ... --service ... --detach --json` 上传已发布源码。GitHub 自动部署尚未连接，不把一次上传称作已配置自动更新。必须检查对应 deploymentId 的 `SUCCESS`，再核对公网认证、Worker、源码、APK 和实际重启后数据，才认为本次上线验收通过。

Android 0.18.2 预设正式地址。首次安装直接连接；覆盖安装仅把此前固定的预览地址迁到正式服务，自定义地址继续保留，需要在新域名重新登录。桌面历史不会自动迁入；旧云端数据迁移使用 SQLite 一致性备份，只向没有数据库的新卷上传，不覆盖目标数据。

## 历史预览 VM

用户已领取分配的 VM，并明确批准向该机传输模型配置、独立管理员初始密码和启动服务。工作台地址为 https://preview-ff00a4ca69754797.up.railway.app；Android 0.18.1 预设同一地址。应用位于 `/app/devflow`，配置文件 `/root/.config/devflow/runtime.env` 权限为 600，数据库与源码缓存位于 `/root/.local/share/devflow`，均在公开目录之外。电脑旧数据没有自动迁移。

此 VM 直接运行应用进程，没有执行 Docker 镜像。`run_vm.sh` 对同一配置目录加锁，启动 `run_service.py`，应用退出后等待 5 秒重启。它记录外层与应用 PID，接到终止信号时停止自己的子进程。按平台提供的运行说明，必须脱离 SSH 会话启动，使用 `setsid`，避免命令执行器清理 `nohup` 后台任务：

```bash
cd /app/devflow
setsid -f sh infra/cloud/run_vm.sh </dev/null >>/root/.config/devflow/service.log 2>&1
```

`run_service.py` 管理 API、Worker 和 Next.js；只有 Next.js 监听公网端口 8080。公网认证与下载检查通过后才交付 APK。模型认证检查只访问官方 `/models`，没有发送聊天请求。

外层守护负责应用进程退出后的重启。曾观察到平台恢复后只剩独立 API，公网回到占位页，因此按 [Railway 官方启动钩子说明](https://github.com/railwayapp/cli/blob/master/README.md#cloud-agent-bootstraps)安装 `/etc/railway/bootstrap/startup.sh`，内容来自本仓库 `infra/cloud/startup.sh`。钩子脱离启动会话后调用 `launch_vm.py`，载入私有配置并启动完整应用；运行中的守护持有锁时重复调用直接返回，不中断已有任务。只有没有完整守护时，才终止经路径、命令、监听地址和端口核对的本项目旧独立 API，避免抢占 8000 端口。模拟启动通过后，后来仍观察到公网回到占位页；SSH 恢复应用不构成正式服务稳定性证明，因此迁移到常规服务。

已复现只剩 API 的故障状态，并用实际安装的钩子在最小启动环境中恢复网页、API 和 Worker；重复调用保持同一守护，数据库与模型请求记录保留。20 项部署与进程识别检查通过。未执行实际 VM 睡眠／唤醒测试，平台可用性仍需单独维护；需要更强可用性时使用上面的 Dockerfile 部署到平台持久服务并挂载卷。不要把预览 VM 称为永久免费的高可用服务。

检查和手动调用当前 VM 的启动入口：

```bash
sh -n /etc/railway/bootstrap/startup.sh
sh /etc/railway/bootstrap/startup.sh
tail -n 20 /root/.config/devflow/startup.log
```

新 VM 安装时先检查是否已有启动钩子，保留其他服务的既有内容；不要直接覆盖。当前服务器模型密钥和密码不随启动脚本发布。
