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
LLM_API_KEY=仅填在云平台的服务端变量中
```

启动器强制登录、Secure Cookie、HTTPS Origin，并保持应用自动 GitHub 发布关闭。源码上传使用电脑已有的 Git 登录；该 Git 凭据不会复制到运行服务器。公共仓库同步通常无需 Token，私有仓库应单独配置只读 Token。

平台分配地址且 `/api/auth/session` 返回 auth_enabled=true 后，用该根地址执行 scripts/build-android.ps1 的 -ServerUrl 参数。APK 首次打开直接连接服务，登录后进入工作台，无需填写地址；连接设置仍可用于换服务器。

Railway 的匿名 VM 只用于临时验收。未领取时预览地址限制为创建网络访问，并有构建/领取期限；用户领取后才可分享，不能把临时预览称为永久上线。长期账户、可用余额、存储与续费需在平台确认。

参考：[Dockerfile 构建](https://docs.railway.com/builds/dockerfiles)、[HTTPS 地址](https://docs.railway.com/networking/public-networking)、[持久卷](https://docs.railway.com/volumes)、[临时 VM](https://railway.com/free-vm)。

## 当前已领取的 VM

用户已领取分配的 VM，并明确批准向该机传输模型配置、独立管理员初始密码和启动服务。工作台地址为 https://preview-ff00a4ca69754797.up.railway.app；Android 0.18.1 预设同一地址。应用位于 `/app/devflow`，配置文件 `/root/.config/devflow/runtime.env` 权限为 600，数据库与源码缓存位于 `/root/.local/share/devflow`，均在公开目录之外。电脑旧数据没有自动迁移。

此 VM 直接运行应用进程，没有执行 Docker 镜像。`run_vm.sh` 对同一配置目录加锁，启动 `run_service.py`，应用退出后等待 5 秒重启。它记录外层与应用 PID，接到终止信号时停止自己的子进程。按平台提供的运行说明，必须脱离 SSH 会话启动，使用 `setsid`，避免命令执行器清理 `nohup` 后台任务：

```bash
cd /app/devflow
setsid -f sh infra/cloud/run_vm.sh </dev/null >>/root/.config/devflow/service.log 2>&1
```

`run_service.py` 管理 API、Worker 和 Next.js；只有 Next.js 监听公网端口 8080。公网认证与下载检查通过后才交付 APK。模型认证检查只访问官方 `/models`，没有发送聊天请求。

外层守护处理应用进程退出，不提供跨 VM 睡眠或重启的启动保证。平台恢复时曾出现只剩 API、网页与 Worker 未运行的状态，已在空闲队列下停止经路径和命令核对的本项目 API，并以独立会话恢复完整服务。VM 睡眠、重启或平台故障后应重新启动并核对 `/api/auth/session`、登录、Worker 和下载；需要更强可用性时使用上面的 Dockerfile 部署到平台持久服务并挂载卷。不要把预览 VM 称为永久免费的高可用服务。
