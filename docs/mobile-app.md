# DevFlow 安卓客户端与 HTTPS 部署

手机客户端 v0.18 复用现有 Python 后端（API 版本 0.18.0）和 Next.js 工作台。安卓应用是带原生连接页、导航控制和文件导出的 WebView 客户端：计算和数据保存在服务器，APK 内不含模型密钥或 Python 服务。需要可访问且证书有效的 HTTPS 地址，安卓 8.0 及以上。

## 安卓安装包

最新客户端为 0.18.2 / versionCode 20，安装包位于 `artifacts/mobile/DevFlow-0.18.2-debug.apk`，包名 `com.mysiya.devflow`，沿用原测试签名，可覆盖安装旧版。它是个人测试包，手机实机验收与原生编译核验分别记录。

电脑可访问当前服务的 `/downloads/DevFlow-0.18.2-debug.apk`，或在网页“设置 → 手机应用”下载，然后把文件传到安卓手机。在手机文件管理器打开 APK，按系统提示允许当前来源安装，安装后启动 DevFlow。新包预设 mobile/service.json 中的 HTTPS 地址，第一次直接连接登录页，后续保留登录会话；无需手填地址，也没有内置账号密码。服务器必须已完成启动和认证检查，安装包生成不代表后台已上线。连接菜单仍可更换其他受信任的服务。

工程在 `mobile/android/`。当前包首次启动使用预设地址；只有切换服务器时才需要填写新的 HTTPS 根地址，例如 `https://devflow.example.com`。不能填写 `127.0.0.1:3000`、API 子路径或含密码/查询参数的 URL。客户端先读取 `/api/auth/session`，确认服务启用登录，再载入工作台。登录、源码分析和后台记录使用原接口。

底部提供概览、工作台、知识、记录和更多；更多中可切换仓库，打开代码、记忆审批、评测和设置。原生顶部可以刷新或切换服务器。切换服务会清除本机登录与页面数据，服务器任务保留。关闭客户端不取消已提交的后台任务。返回键先返回网页历史，之后退出客户端。

导出的 JSON 使用安卓系统“保存文件”选择器，只向当前 HTTPS 服务读取，带当前会话 Cookie，不跟随跨域重定向，限制 16 MB；无需存储、相机、联系人或定位权限。源码/GitHub 外链在系统浏览器打开。证书错误、明文 HTTP、文件访问、第三方 Cookie 和任意原生 JS 接口均不启用。

### 编译

工程固定使用 Gradle 9.3.1、Android Gradle Plugin 9.1.0、平台 API 35 和 Build Tools 36.0.0，Java 源码与字节码目标为 17。默认下载项目内的 Temurin JDK 17，也可复用现有兼容 JDK/Gradle；本机使用已有 JDK 25.0.2 和 Gradle 9.3.1。脚本只设置本进程的环境，不改变系统 Java 或全局环境变量。首次下载归档校验 SHA-256，SDK、构建缓存和签名私钥位于忽略的 `mobile/android/.local/`，已有依赖缓存通过只读方式复用。

先阅读并明确同意 [Google Android SDK 许可](https://developer.android.com/studio#downloads)，然后在项目根目录执行：

```powershell
.\scripts\build-android.ps1 -AcceptSdkLicense
```

本机已在聊天中明确同意安装及 SDK 许可。脚本优先识别现有兼容 JDK 和固定版本 Gradle；也可传入 `-JavaHome 'JDK目录' -GradlePath 'gradle.bat完整路径'`，这时只下载 SDK。下载按官方固定归档校验；SDK 大文件分段续传并在合并后核对完整 SHA-256，网络中断不会把不完整归档当作安装完成。

有确定服务器地址时可预填：

```powershell
.\scripts\build-android.ps1 -AcceptSdkLicense -ServerUrl 'https://你的域名'
```

未传 ServerUrl 时默认读取 mobile/service.json，只接受不带凭据、路径或参数的 HTTPS 根地址。脚本只安装本工程需要的 SDK 包，生成本地测试签名，执行 APK 构建、Android Lint 和签名验证。成功后安装包复制到 `artifacts/mobile/DevFlow-0.18.2-debug.apk`。这是用于个人安装联调的测试包；商店分发需要独立维护正式签名、应用说明和相应发布流程。保留当前测试签名私钥才能覆盖安装后续同签名版本。

`scripts/verify-android.py` 另行核对包名、版本、最小系统、权限、编译后的 Manifest、签名、Lint 和解压后的凭据匹配，还回读所有 DEX 中的预设地址；证明保存在 `artifacts/mobile/android-apk-proof-v182.json`，旧版证明保留。0.18.2 的实际 Lint 报告为 0 错误、2 警告：低系统忽略返回属性及开启 JavaScript；旧包报告的 4 项提示保留在原证明中。Next.js 工作台需要 JavaScript，WebView 限制 HTTPS 同域且没有原生 JS 接口，没有添加警告屏蔽。按 [Android 备份规则](https://developer.android.com/identity/data/autobackup)明确排除云备份和设备迁移中的应用数据，编译结果已核对；实际厂商系统行为仍需手机验证。

地址校验可在没有 SDK 时用 JDK 单独验证：

```powershell
New-Item -ItemType Directory -Force artifacts/android-policy-tests
javac -encoding UTF-8 -d artifacts/android-policy-tests mobile/android/app/src/main/java/com/mysiya/devflow/ServerAddress.java mobile/android/tests/ServerAddressTest.java
java -cp artifacts/android-policy-tests ServerAddressTest
```

## 当前上线服务

入口为 [DevFlow 工作台](https://devflow-production-ef63.up.railway.app)，[安卓 0.18.2 安装包](https://devflow-production-ef63.up.railway.app/downloads/DevFlow-0.18.2-debug.apk) 使用同一预设地址。首次打开输入管理员账号 `admin` 和交付的独立密码即可使用。密码只保存在本机忽略文件 `artifacts/deploy/login-info.md` 与服务器私有配置中，不发布到 GitHub。

旧预览 VM 曾通过公网接口检查，后来仍返回平台欢迎页，现已改用 Railway 常规服务，配置 Dockerfile、持久卷、健康检查和重启策略。2026-10-10 已确认对应部署为 SUCCESS，公网登录、Worker、源码同步与 APK 下载通过；一次真实 DeepSeek V4 Pro 源码分析完成，主动断开 SSE 后按事件编号重连，完整重放后续 21 个事件。实际重启服务后，原登录会话、完成与失败记录、模型用量和 166 个源码文件保持一致。0.18.2 编译、签名、Lint、预设域名和凭据扫描已通过；安卓实机操作仍需验证。

服务器使用独立 SQLite 和关键词检索，已接入 Mysiya/DevFlow 公共仓库及 README 知识库。电脑上的历史回答、记忆、知识库和 Milvus 索引没有自动迁入；电脑与手机连接此云端服务时共享云端新记录。覆盖安装仅自动替换原固定预览地址，自定义服务地址继续保留，新域名需要重新登录。GitHub Actions 已运行 PR 和主分支检查；Railway 已连接 main 并从源码构建，推送自动部署仍待核验。云端真实 PR 与发布前协作检查已完成，记录可在网页或 APK 的历史中查看。运行配置见 [云部署说明](../infra/cloud/README.md)，验收范围见 [验证记录](verification.md)。

## 自有服务器部署

准备已指向服务器公网 IP 的域名，服务器具备 Docker Compose，并允许公网访问 80/443。实际部署前需要确定服务器地址、连接方式和目标目录；本地创建部署文件不会修改远程服务器。

上传源代码到服务器，在项目根目录复制 `infra/mobile/.env.example` 为 `.env.mobile`，填写域名、数据库密码、初始管理员密码和模型配置。数据库密码使用足够长的 URL 安全随机字符（例如十六进制），管理员密码独立且至少 12 位。模型密钥仅填写服务器环境文件，不编入客户端或前端静态资源。

```bash
docker compose --env-file .env.mobile -f compose.yaml -f compose.mobile.yaml config --quiet
docker compose --env-file .env.mobile -f compose.yaml -f compose.mobile.yaml up -d --build
```

覆盖文件强制开启登录、Secure Cookie，允许写请求的 Origin 只包含该 HTTPS 域名，GitHub 写回继续关闭。Caddy 对域名提供 HTTPS，代理到前端，前端经 Docker 内部网络访问 API；公网入口为 80/443，后端、前端与 PostgreSQL 保留原回环端口绑定。Worker 与 API 使用同一认证、数据库和模型配置。

服务器首次启动创建管理员。浏览器打开域名确认可登录，连接仓库并核对 API/Worker，然后用该域名编译预设地址 APK，或通过连接菜单切换旧客户端。SDK 下载、Docker 配置准备或 APK 构建不会调用模型；实际发起 live 分析才调用已配置模型。

这是新的 PostgreSQL 部署环境，不自动复制现有电脑的 SQLite、模型密钥、源码缓存、知识库或向量索引。若要保留本地已有回答/记忆/样本集，应单独准备备份与迁移，并在确定目标服务器后核对。默认部署采用关键词检索；本地 Milvus/Embedding 迁移需另外配置，不能把容器配置文件当作已经完成的生产部署。

## 手机网页版与离线

同一个手机界面也可直接通过浏览器访问。设置中的“手机应用”提供浏览器安装入口和 Safari 添加到主屏幕说明；APK 中不再提示安装第二个网页版。

Service Worker 仅缓存公共离线提示页和图标，不缓存 API、SSE、原回答、仓库源码、登录页面或问题提交，不自动补发离线请求。打开中的页面离线时提示连接状态；重新打开且网络不可用时显示公共提示页。离线不能分析项目，后台任务仍由可运行的服务器处理。

浏览器安装要求参见 [Next.js PWA 指南](https://nextjs.org/docs/app/guides/progressive-web-apps)与 [MDN 安装要求](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable)；安卓 WebView 接入参见 [Android 官方文档](https://developer.android.com/develop/ui/views/layout/webapps/webview)。

验证应区分前端构建/接口、原生构建/签名，以及手机实机登录、键盘、返回、SSE 重连与 JSON 保存。未连接真实手机或未部署 HTTPS 时，不声称实机和外网使用已验收。
