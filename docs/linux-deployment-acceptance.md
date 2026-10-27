# Linux 部署验收（2026-10-27）

Windows / macOS 继续使用桌面客户端，Linux 运行无头 Switch 和已有 HQHarness Team 网页服务。实现和运行方式见 [Linux 部署说明](../deploy/linux/README.md)及 [Team 集成说明](../integrations/harness-team/README.md)。

## 来源与镜像

Switch 服务提交为 `9e94c83c13bac8d3681ee4ceb7c79a029ecaa479`，最终 Team 集成提交为 `207b4db768758b5c296ab5857aaa21b83e69208f`。两提交之间全部 Switch 构建输入字节相同。Switch 在目标 Linux amd64 的 Debian bookworm 构建容器中使用锁定、离线依赖完成原生测试和发布构建；不是桌面客户端加虚拟显示器。

交付四份实际运行的 Linux amd64 镜像：

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
