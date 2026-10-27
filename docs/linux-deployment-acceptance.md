# Linux 部署验收（2026-10-27）

Windows / macOS 继续使用桌面客户端，Linux 运行无头 Switch 和已有 HQHarness Team 网页服务。实现和运行方式见 [Linux 部署说明](../deploy/linux/README.md)及 [Team 集成说明](../integrations/harness-team/README.md)。

## 首次交付的来源与镜像

Switch 服务提交为 `9e94c83c13bac8d3681ee4ceb7c79a029ecaa479`，最终 Team 集成提交为 `207b4db768758b5c296ab5857aaa21b83e69208f`。两提交之间全部 Switch 构建输入字节相同。Switch 在目标 Linux amd64 的 Debian bookworm 构建容器中使用锁定、离线依赖完成原生测试和发布构建；不是桌面客户端加虚拟显示器。

首次交付包含以下四份 Linux amd64 镜像：

- `huaqing-switch-server:0.1.1-linux-9e94c83`
- `huaqing-team-gateway:0.2.1-rc.2-switch-207b4db`
- `huaqing-team-worker:0.2.1-rc.2-switch-207b4db`
- `huaqing-team-edge:0.2.1-rc.2`

镜像归档为 `images/huaqing-switch-linux-x86-983d2880071273074bc95b084ebb3564d982f4827498113a49f90795162f07ca-20261027142416.tar.gz`，大小 `585615102` 字节，SHA256 为 `983d2880071273074bc95b084ebb3564d982f4827498113a49f90795162f07ca`。gzip/tar 校验及实际 `docker image load` 通过，导入后四份镜像身份与导出前一致；镜像清单逐一记录来源和层。运行时数据库、用户卷及私密凭证文件不包含在镜像归档中。

## 实际验证

- 服务端 24 项测试、严格 Clippy、格式检查，以及桌面 HQ 16 项回归通过；真实原生进程的 Cookie、双凭证分权、健康检查、SIGTERM 与监听释放验证通过。
- 前端 24 项针对性检查、类型检查、独立部署版及桌面渲染器构建通过。部署版复用连接和用量界面，不调用桌面 IPC。
- 隔离 Linux 网络中的真实 Team profile 和已安装 Team runtime/Pi 完成三协议工具流、用量解析、跨网关取消、旧会话版本撤销和重启保留验收。8 次 fixture 调用产生 8 条记录：7 成功、1 取消，输入 80、输出 35、总计 115 Token。生产镜像的角色代码与验收产物逐字节一致。
- 真实服务器浏览器完成登录、连接列表、用量筛选、主题及登出检查。迁移前后分别观察到 0 / 1 个连接；Cookie 属性正确，凭证未进入浏览器持久存储，原生调用、浏览器错误及模型写入均为 0。浏览器进程在服务器执行，管理凭证未离开服务器；TLS 另以部署 CA 完整验证。
- 生产只读验收 43 项全部通过：原有 1 个账号及密码字段、加密模型目录、私密凭证、2 个会话标识和原 named volumes 保持不变；1 个模型迁入 Switch；旧 worker 停用保留，服务健康并启用 systemd / Linger 自动启动。没有为验收重启整台服务器。

最终提交的 [主 CI 40458091428](https://github.com/hqzyai/huaqing-switch/actions/runs/40458091428) 六项作业及 [Linux Server CI 40458091420](https://github.com/hqzyai/huaqing-switch/actions/runs/40458091420) 全部通过。Linux CI 同时覆盖服务鉴权、共享 HQ 业务、网页入口及 Team 目录适配行为。

所有推理验收使用确定性 fixture，没有调用付费生产模型。生产最终检查为只读；上述结果不表示真实供应商账单或付费调用已经验证。

## 运维边界

生产配置完整合并原 Team base 与 LAN 覆盖项，保持原端口和局域网 HTTP 策略；运行不能只使用旧 base compose 文件。Switch 管理入口使用独立的局域网 HTTPS，宿主防火墙只放行实际 LAN 网段。常驻 Team 网关只读取 Switch 推理凭证和版本映射，worker 保留原用户能力凭证，不持有供应商或 Switch 管理凭证。

数据库在线快照、私密配置、worker 配置及停止后的 home/workspace 卷备份保留在服务器私密目录；没有加入源码或镜像。回滚要成对恢复网关与 worker，并保留原身份、签名密钥、用户卷和模型映射，不执行 `down -v`。镜像归档附 SHA256、脱敏验收报告与部署说明；主机地址、登录凭证和用户数据不随源码记录。

## 公开端口配置变更（2026-10-27）

按用户指定，Switch 部署模板的公开 HTTPS 端口从 `18444` 改为 `28001`。Compose 的宿主端口映射、Caddy 监听端口和 `HQ_SWITCH_PUBLIC_ORIGIN` 同步使用 `28001`；宿主防火墙仍仅放行实际局域网网段到该端口。内部管理端口 `8080`、推理端口 `17621`、HTTPS 协议及 HQHarness 原访问端口均保持不变。

本次调整属于部署配置变更，无需重建业务镜像；上述镜像内容、归档校验和及历史验收事实保持原记录。实机已通过 CA 证书链校验、同源登录与 CSRF、1 个连接、用量筛选、主题及登出验证，浏览器错误和模型写入均为 0；旧 Origin 被拒绝，旧端口不再监听且对应防火墙规则已删除。两个原会话标识继续保留，服务启动配置及 HQHarness 管理链接均已同步。

## 管理凭证格式与轮换（2026-10-27）

按用户要求，服务支持完整的 `sk-` 加 64 位随机十六进制管理凭证，同时兼容已有的 64 位十六进制管理凭证。认证比较完整字符串；去掉前缀不能登录。推理凭证仍保持原格式，并拒绝管理和推理复用相同随机部分。初始化只为新部署创建带前缀的管理凭证，重复执行保留已有文件。

实现提交为 `70d4ad1eaa6598d09a5491704f366f4eb390313e`。26 项 Rust 检查、严格 Clippy、初始化回归及实际原生 HTTP 登录通过；在 Linux 原生构建后轮换生产管理凭证，确认新凭证被接受、旧凭证拒绝、管理凭证不能推理、模型配置及机器推理凭证保持不变。服务器内真实浏览器完成新凭证登录、1 个连接、用量筛选及登出验证，原有 2 个会话仍保留，没有调用付费模型。

当前 Switch 镜像为 `huaqing-switch-server:0.1.1-linux-70d4ad1`，另三份镜像沿用此前交付。新版归档为 `images/huaqing-switch-linux-x86-4f155cd50c73e929d2472c338fcdf3ac67d92db7588a105d304ca97af030dbef-20261027145915.tar.gz`，大小 `585612926` 字节，SHA256 为 `4f155cd50c73e929d2472c338fcdf3ac67d92db7588a105d304ca97af030dbef`；gzip/tar、实际导入及四份镜像身份核对通过。旧归档保留作回滚记录。回滚不支持新格式的旧 Switch 镜像时，还须恢复受保护的旧管理凭证备份。

真实管理凭证只在服务器私密文件中保存，用户要求的凭证回复单独完成；没有写入源码、镜像或交付清单。Linux 服务 CI 见 [40463221777](https://github.com/hqzyai/huaqing-switch/actions/runs/40463221777)。

## HTTP 直连与 HTTPS 共用 28001（2026-10-27）

按用户确认，HTTP 入口直接返回页面，不跳转到 HTTPS；已有 HTTPS 同时保留。实现提交为 `e7e86c9d4b6f8b32fcec281a575ee63d72efa883`。Nginx 1.30.6 在唯一公开的 `28001/tcp` 上区分明文和 TLS，分别交给 Caddy 的内部 HTTP / HTTPS site；Switch 两个管理监听器固定各自 Origin 和 Cookie 策略。HTTP 需要显式配置私网同 host / port 的 Origin / Bind，单独运行默认关闭；不依据客户端转发头选择会话策略。

HTTPS 保留 `__Host-` / Secure Cookie；HTTP 使用独立的 HttpOnly / SameSite Cookie 和会话命名空间，两个方向改名重放均被拒绝。HTTP 按指定的受信任 LAN 方式使用，凭证会以明文传输；原有 LAN 防火墙范围和 TLS CA 均保留。Nginx 以非 root 用户运行，只有入口服务发布端口，Switch 管理和机器推理端口不发布。

服务端 29 项测试、严格 Clippy、格式检查，以及真实主程序 13 项双入口验收通过。Linux 原生构建后，完整隔离部署的 19 项检查通过，包括证书链验证、HTTP 无跳转、登录、元数据、用量、跨 Origin 拒绝和双向会话隔离。生产 HTTP、HTTPS 的独立浏览器均完成登录、1 个连接、打开编辑后取消、用量、主题和登出验收；没有模型写入、付费调用、浏览器错误或凭证导出。Mac 通过局域网实测 HTTP 及使用原 CA 校验的 HTTPS 均返回成功。

生产升级保留数据库、管理和推理凭证、版本映射及原有 2 个会话。验收时 HQHarness 的现有入口已为 `28003`，另保留 `18443`；本次只修改 Switch，未回退 HQHarness 的外部端口配置。Linux 服务 CI 见 [40475913868](https://github.com/hqzyai/huaqing-switch/actions/runs/40475913868)。

本次生产只读审计 67 项通过，包含实际入口、镜像、会话、凭证隔离和自动启动配置。当前 Switch 镜像为 `huaqing-switch-server:0.1.1-linux-e7e86c9`；新增 Nginx 镜像后离线包包含五个已核验的 Linux amd64 镜像。归档为 `images/huaqing-switch-linux-x86-4478f43c082a344ef6551aa17a393c7d07d902cd04802706d73ff31164efab0b-20261027170127.tar.gz`，大小 `611626983` 字节，SHA256 为 `4478f43c082a344ef6551aa17a393c7d07d902cd04802706d73ff31164efab0b`。gzip/tar、实际导入及全部五份镜像身份核对通过；旧归档保留，私密状态不包含在镜像中。

实现提交的[主 CI 40475914110](https://github.com/hqzyai/huaqing-switch/actions/runs/40475914110)六项作业及上述 Linux Server CI 均已成功。
