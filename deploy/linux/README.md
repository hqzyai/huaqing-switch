# Linux 部署

Windows / macOS 使用 Tauri 客户端，Linux 使用 `server/` 无头程序和网页管理界面。两种入口复用 `src-tauri/src/hq.rs` 与 `hq/` 中的连接、协议转发和用量逻辑；服务端不启动 Tauri、GTK、托盘或虚拟显示器。

## 构建

从已记录的 Git 提交构建 Linux amd64 镜像：

```sh
docker build --platform linux/amd64 \
  --build-arg SOURCE_COMMIT="$(git rev-parse HEAD)" \
  -f deploy/linux/Dockerfile -t huaqing-switch-server:<source-commit> .
```

Node、Rust 与 Debian 基础镜像固定到摘要，前后端分别使用锁文件。Rust 构建不需要 GTK/WebKit。`Dockerfile.artifact` 接收已验收的 `huaqing-switch-server`、`dist-server/` 和 `LICENSE`；二进制必须来自同一源码提交，并在 Debian bookworm 或更老的兼容 glibc 上构建。Linux Server CI 使用 Ubuntu 22.04，产物附源码提交与逐文件 SHA256。不要直接将 macOS/Windows 或较新 glibc 的二进制装入运行镜像。

## 部署目录

部署根目录统一为 `/home/ubuntu/Workspace/huaqing-switch`。运维命令和 `huaqing-switch.service` 都从这个目录运行：

```sh
cd /home/ubuntu/Workspace/huaqing-switch
./compose config --quiet
./compose ps
```

`current` 指向 `releases/<version>`；顶层 `.env`、`compose.yml`、`Caddyfile`、`nginx.conf` 以相对符号链接指向 `current/` 中同名文件，确保直接进入根目录时读取当前版本。把本目录的 `compose` 可执行入口安装到部署根目录；它固定专用 rootless socket，并将 Compose 的项目目录解析到当前版本的物理路径，保留相对挂载路径和配置哈希，避免仅改变入口目录就触发容器重建。运维统一使用 `./compose`。`state/` 保存私密数据，`backups/`、`logs/`、`acceptance/` 分别保存回滚快照、运维日志和验收记录。升级保留根目录与状态目录，原子切换 `current` 后从根目录运行入口；不要手改历史版本文件。Nginx 配置需为容器 UID 1000 可读（0644），凭证保持 0600。

用户级启动单元使用本目录的 `huaqing-switch.service` 模板，安装到 `~/.config/systemd/user/huaqing-switch.service` 后执行 `systemctl --user daemon-reload` 和 `systemctl --user enable huaqing-switch.service`。已有运行容器仅调整入口路径时无需重启；先比较入口解析后的配置和当前容器配置哈希，再使用 `./compose up -d --no-build --no-recreate --wait` 验证，确认容器 ID 和启动时间不变。首次启动或版本升级使用 `./compose up -d --no-build --wait`。单元启动超时覆盖 330 秒停止宽限与 120 秒健康等待。

镜像归档仍使用下文指定的 `/home/ubuntu/Workspace/images/`，其位置与部署根目录分别记录。

## 运行

本部署与 HQHarness 共用专用的 **rootless Docker**，通过已有 `huaqing-team-control` 内部网络连接。普通宿主 Docker 的管理套接字不挂载到 Switch。以下变量由部署者填写，使用实际已核验的镜像标签：

```sh
export DOCKER_HOST=unix:///run/user/$(id -u)/huaqing-docker/docker.sock
export HQ_SWITCH_IMAGE=huaqing-switch-server:<source-commit>
export HQ_SWITCH_EDGE_IMAGE=huaqing-team-edge:0.2.1-rc.2
export HQ_SWITCH_LAN_ADDRESS=<server-lan-ip>
export HQ_SWITCH_STATE_DIR=/home/ubuntu/Workspace/huaqing-switch/state
docker pull --platform linux/amd64 nginx:stable-alpine@sha256:84ed8c0dd296c188bfc24ed506f46589e9285fb0b11cf3a04d91d524d3067e11
docker tag nginx:stable-alpine@sha256:84ed8c0dd296c188bfc24ed506f46589e9285fb0b11cf3a04d91d524d3067e11 huaqing-switch-ingress:nginx-1.30.6-84ed8c0dd296
python3 deploy/linux/initialize-state.py "$HQ_SWITCH_STATE_DIR"
docker compose -f deploy/linux/compose.yml config --quiet
docker compose -f deploy/linux/compose.yml up -d --no-build --wait
```

Nginx 固定为官方 1.30.6 的 linux/amd64 摘要，导入后使用指定本地标签；离线镜像归档也必须包含该标签，以便 `docker save/load` 后直接启动。把环境变量存入部署目录的私密环境文件，供重启和运维重复使用；不要把凭证本身写进环境变量或 Compose 文件。状态初始化会保留已有文件，拒绝不安全的权限或相同的两份凭证。容器内的 HTTPS 管理上游监听 `8080`、HTTP 管理上游监听 `8081`、推理接口监听 `17621`。只有 Nginx 入口的 `28001` 绑定局域网，按 TLS ClientHello 将连接分别转给 Caddy 的 HTTPS / HTTP 入口；其他端口不发布到宿主。

新版本还必须提供独立的供应商凭证加密主密钥。先运行上述初始化脚本：首次创建 `$HQ_SWITCH_STATE_DIR/private/provider-encryption-key`，内容为 32 个原始随机字节（不是十六进制字符串，不带换行）；已有文件只校验、不覆盖。文件必须属于部署用户、为普通文件且权限严格为 0600，绝对路径各级不能包含符号链接。主密钥不能复用管理、推理或供应商 API Key。Compose 通过 `HQ_SWITCH_PROVIDER_KEY_FILE=/run/secrets/provider-encryption-key` 指定容器内路径，并从 `state/private/` 只读挂载，位于 `/data` 之外。独立运行服务也必须设置此变量，不能把主密钥写入环境变量、镜像或日志。升级保留原主密钥；丢失后应恢复与数据库匹配的原密钥，重新生成不能解密旧记录。

本 Compose 按局域网直连需求显式启用 `http://<server-lan-ip>:28001`，返回 HTTP 内容，不跳转；`https://<server-lan-ip>:28001` 同时保留。HTTP 会明文传输管理凭证和页面内容，只在已授权的受信任内网使用，并保留下述防火墙限制。HTTP 开关由 `HQ_SWITCH_HTTP_ORIGIN` 和 `HQ_SWITCH_HTTP_BIND` 成对控制，单独运行服务时默认关闭；HTTP Origin 必须是私网 IPv4 或 loopback，且 host / port 与主 HTTPS Origin 一致。只需 HTTPS 时，应同时移除 Compose 的这两个变量、Caddy 的 HTTP site 和 Nginx 的明文分流配置。

同一入口的 `/model-api/` 只转发到原有机器推理监听器，使用独立推理凭证，不能用管理凭证调用。业务服务使用 `/model-api/hq/services/<connection-id>/v1` 稳定地址调用现有文本生成协议；不再保存上游文本模型 API Key。模型、密钥、URL 和预算在 Switch 修改后对后续调用生效，原 Harness 的版本固定目录继续保留。跨 rootful / rootless daemon 的调用需按 `integrations/multi-app/network-policy.py` 核验真实网桥后限域放行已有 `28001/tcp`，不得直接对所有容器或公网开放。详细迁移边界与各应用的持久配置脚本见 `integrations/multi-app/`。

首次登录使用 `$HQ_SWITCH_STATE_DIR/private/admin-token` 中的管理凭证，通过 SSH 私下读取。新管理凭证格式为 `sk-` 加 64 位十六进制随机串（32 随机字节）；登录和 CLI Bearer 均须使用含 `sk-` 的完整字符串，不能去掉前缀。原有无前缀 64 位十六进制管理凭证仍兼容，重复运行初始化脚本会保留现有值，不自动轮换。机器推理凭证保持无前缀的 64 位十六进制格式；两种凭证的随机部分必须独立，不能仅通过增减 `sk-` 复用同一串随机值。HTTPS 使用 `__Host-hq_switch_session`（HttpOnly / Secure / SameSite=Strict）；HTTP 使用独立的 `hq_switch_http_session`（HttpOnly / SameSite=Strict，无 Secure），会话不能跨入口重放。两端保留各自同源 CSRF 校验，共享登录限流和会话总量限制；传输策略由服务监听器决定，不信任客户端转发头。网页不会存储管理凭证。`inference-token` 供 HQHarness 网关及已接入的业务服务读取，不交给浏览器或 Harness worker；业务服务不能持有 `admin-token`，两种凭证不能互用。

Caddy 使用内部 CA 为局域网地址签发证书。导出新建 `edge-tls` 卷的 `/data/caddy/pki/authorities/local/root.crt`，核对后导入访问设备的受信任根证书库；保留 CA 的私钥卷用于续签。TLS 验收应使用 `curl --cacert <root.crt>`，不能用跳过证书校验代替。已有企业证书时可在入口层替换为组织管理的 TLS 配置。

若宿主启用了入站防火墙，仅放行实际局域网网段到服务器 `28001/tcp`；不要为此关闭防火墙或扩大到公网。Compose 的端口绑定不能替代宿主防火墙规则。入口桥接网络保留端口发布能力；rootless Docker 中只连接 `internal` 网络的容器不会发布宿主端口。

Switch 和 Caddy 的容器 UID 0 仅用于上述 rootless daemon，映射到宿主非特权部署用户，使 0600 凭证和专用 TLS 卷无需放宽权限；同时根文件系统只读、移除全部 capabilities、禁止提权，并设置资源限制。Nginx 无需持久状态和低端口，以 UID/GID 1000:1000 运行。不要原样迁移到 rootful daemon。

## 数据、升级与恢复

`state/data/switch.sqlite` 保存加密的供应商凭证（`formatVersion: 2`、连接内的 `credential`）和统计，目录 0700、文件 0600。它与桌面数据库独立；服务不扫描或导入其他用户的桌面配置。凭证不进入响应、用量表、镜像和导出清单。数据库启用 WAL、FULL 同步和进程独占锁；不要将同一状态目录同时挂载给多个活动实例。

升级前用 SQLite 在线备份或停止服务后备份整个私密状态目录，同时记录镜像 ID、Compose/环境文件及 TLS 卷。升级固定镜像标签，等待健康检查并确认连接和用量后再交付。原有请求最多等待 300 秒，容器停止期限为 330 秒；不要对正在调用的 worker 强制重建。

交付和留存备份时，将密文数据库与其对应的 `provider-encryption-key` 分开安全保管，分别控制访问权限并记录配对关系；仅有数据库或错误的主密钥无法恢复供应商凭证。若先备份整个状态目录，需将主密钥移入独立的私密密钥备份，避免与数据库归档一同分发。历史版本的数据库、迁移计划和私密备份可能仍包含明文，应继续按凭证保护，不因新版本启用加密就视为已清除。

`integrations/multi-app/` 中历史迁移工具读取原始 `hq.connections.v1` 的逻辑只适用于旧明文格式；读取器遇到 `formatVersion` 或 `credential` 会拒绝继续。不要把密文封套当作上游 API Key，也不要在这些一次性迁移工具中新增解密入口。连接的受支持管理操作使用带鉴权的 Switch 管理 API；无需读取原始配置的既有 embedding 证据校验仍可独立使用。

HQHarness 集成应保留原加密目录、用户身份、签名密钥、会话与工作区卷，并固定旧会话的模型版本。旧镜像与旧 worker 容器留作回滚；不要执行 `down -v` 或删除用户卷。具体集成代码和验收由 `integrations/harness-team/` 提供。

离线镜像交付使用 `docker image save ... | gzip`，先写任务专用 `.part`，检查 gzip/tar、镜像平台、标签和 ID，再原子改名并附 SHA256。镜像归档不包含私密状态卷；恢复业务数据需单独保管上述备份。用专用 daemon 导入后再次核对镜像 ID，并执行健康、鉴权、模型目录及实际模拟推理验收。

镜像归档统一保存为 `/home/ubuntu/Workspace/images/huaqing-switch-linux-x86-<sha256>-<timestamp>.tar.gz`。`sha256` 为最终 gzip 文件的完整 64 位小写 SHA256，`timestamp` 按北京时间使用 `YYYYMMDDHHmmss`。校验文件为归档全名追加 `.sha256`；镜像清单和交付说明沿用相同文件名前缀。重命名交付时同步所有当前路径引用，保持归档内容不变。
