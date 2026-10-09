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
