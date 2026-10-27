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

## 运行

本部署与 HQHarness 共用专用的 **rootless Docker**，通过已有 `huaqing-team-control` 内部网络连接。普通宿主 Docker 的管理套接字不挂载到 Switch。以下变量由部署者填写，使用实际已核验的镜像标签：

```sh
export DOCKER_HOST=unix:///run/user/$(id -u)/huaqing-docker/docker.sock
export HQ_SWITCH_IMAGE=huaqing-switch-server:<source-commit>
export HQ_SWITCH_EDGE_IMAGE=huaqing-team-edge:0.2.1-rc.2
export HQ_SWITCH_LAN_ADDRESS=<server-lan-ip>
export HQ_SWITCH_STATE_DIR=/home/ubuntu/Workspace/huaqing-switch/state
python3 deploy/linux/initialize-state.py "$HQ_SWITCH_STATE_DIR"
docker compose -f deploy/linux/compose.yml config --quiet
docker compose -f deploy/linux/compose.yml up -d --no-build --wait
```

把环境变量存入部署目录的私密环境文件，供重启和运维重复使用；不要把凭证本身写进环境变量或 Compose 文件。状态初始化会保留已有文件，拒绝不安全的权限或相同的两份凭证。服务默认在容器内监听 `8080` 管理接口、`17621` 推理接口，只有 Caddy 的 `28001` 绑定局域网；推理端口不发布到宿主。

首次登录使用 `$HQ_SWITCH_STATE_DIR/private/admin-token` 中的管理凭证，通过 SSH 私下读取。服务使用 HttpOnly / Secure / SameSite Cookie、同源 CSRF 校验、登录限流和会话过期；网页不会存储管理凭证。`inference-token` 只供 HQHarness 网关读取，不交给网页或 worker，两种凭证不能互用。

Caddy 使用内部 CA 为局域网地址签发证书。导出新建 `edge-tls` 卷的 `/data/caddy/pki/authorities/local/root.crt`，核对后导入访问设备的受信任根证书库；保留 CA 的私钥卷用于续签。TLS 验收应使用 `curl --cacert <root.crt>`，不能用跳过证书校验代替。已有企业证书时可在入口层替换为组织管理的 TLS 配置。

若宿主启用了入站防火墙，仅放行实际局域网网段到服务器 `28001/tcp`；不要为此关闭防火墙或扩大到公网。Compose 的端口绑定不能替代宿主防火墙规则。入口桥接网络保留端口发布能力；rootless Docker 中只连接 `internal` 网络的容器不会发布宿主端口。

Compose 的容器 UID 0 仅用于上述 rootless daemon，映射到宿主非特权部署用户，使 0600 凭证和专用 TLS 卷无需放宽权限；同时根文件系统只读、移除全部 capabilities、禁止提权，并设置资源限制。不要原样迁移到 rootful daemon。

## 数据、升级与恢复

`state/data/switch.sqlite` 包含供应商凭证和统计，目录 0700、文件 0600。它与桌面数据库独立；服务不扫描或导入其他用户的桌面配置。凭证不进入响应、用量表、镜像和导出清单。数据库启用 WAL、FULL 同步和进程独占锁；不要将同一状态目录同时挂载给多个活动实例。

升级前用 SQLite 在线备份或停止服务后备份整个私密状态目录，同时记录镜像 ID、Compose/环境文件及 TLS 卷。升级固定镜像标签，等待健康检查并确认连接和用量后再交付。原有请求最多等待 300 秒，容器停止期限为 330 秒；不要对正在调用的 worker 强制重建。

HQHarness 集成应保留原加密目录、用户身份、签名密钥、会话与工作区卷，并固定旧会话的模型版本。旧镜像与旧 worker 容器留作回滚；不要执行 `down -v` 或删除用户卷。具体集成代码和验收由 `integrations/harness-team/` 提供。

离线镜像交付使用 `docker image save ... | gzip`，先写任务专用 `.part`，检查 gzip/tar、镜像平台、标签和 ID，再原子改名并附 SHA256。镜像归档不包含私密状态卷；恢复业务数据需单独保管上述备份。用专用 daemon 导入后再次核对镜像 ID，并执行健康、鉴权、模型目录及实际模拟推理验收。

镜像归档统一保存为 `/home/ubuntu/Workspace/images/huaqing-switch-linux-x86-<sha256>-<timestamp>.tar.gz`。`sha256` 为最终 gzip 文件的完整 64 位小写 SHA256，`timestamp` 按北京时间使用 `YYYYMMDDHHmmss`。校验文件为归档全名追加 `.sha256`；镜像清单和交付说明沿用相同文件名前缀。重命名交付时同步所有当前路径引用，保持归档内容不变。
