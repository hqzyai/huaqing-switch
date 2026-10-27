# HQHarness Team 接入 Huaqing Switch

此目录是已部署 HQHarness Team 0.2.1-rc.2 的扩展源码，不包含 Agent runtime。Linux Switch 独立运行；Windows 和 macOS 继续使用桌面版。Team 网关从 Switch 读取经过校验的模型目录，经固定连接 ID 和版本转发原生请求，所有转发仍由 Switch 原有网关和用量观察器处理。

## 运行方式

`index.mjs` 是网关和 worker 共用的唯一适配器源码，部署为 `/opt/dsh/node_modules/@hqzyai/huaqing-switch-team/`。网关读取 `TEAM_SWITCH_URL` 与私密 `TEAM_SWITCH_TOKEN_FILE`；worker 仍只持有其原有、用户独立的 `DSH_TEAM_TOKEN`，不会读取 Switch 凭证或上游 API Key。三协议使用既有 Pi 的 OpenAI Chat、Responses 和 Anthropic 适配器，不转换响应协议。

| 网关配置                     | 含义                                                           |
| ---------------------------- | -------------------------------------------------------------- |
| `TEAM_SWITCH_URL`            | 明确配置的内部 Switch 源站，例如 `http://huaqing-switch:17621` |
| `TEAM_SWITCH_TOKEN_FILE`     | 仅网关可读的 0600 推理凭证文件                                 |
| `TEAM_SWITCH_ALIASES_FILE`   | 迁移生成的旧服务/模型 ID 到固定连接版本的映射文件              |
| `TEAM_SWITCH_MANAGEMENT_URL` | 给管理员显示的 HTTPS Switch 管理地址，不附加凭证               |
| `TEAM_SWITCH_TIMEOUT_MS`     | 目录请求超时，1000–120000 毫秒，默认 10000                     |
| `TEAM_SWITCH_CACHE_MS`       | 原子目录快照有效期，0–15000 毫秒，默认 1000                    |

启用后，普通成员仍在原会话中选择模型，浏览器模型目录不包含路由地址或密钥。Team 管理页显示可点击的 Switch 管理地址并禁用旧供应商表单；对应旧配置写入和模型发现接口也返回明确拒绝。没有配置 `TEAM_SWITCH_URL` 时，原 Team 模型管理行为保持不变。

目录响应限定 2 MiB、100 个连接，逐一校验模型 ID、原生协议、预算及服务地址。目录只可指向配置的内部 Switch 源站及精确的连接/版本路径。刷新失败或配置删除均关闭相关路由，不回退供应商直连，不重发流式请求。已接纳的请求保持解析时的固定版本，Switch 在发送前再次验证版本。

## 可重放安装

`baseline-sha256.json` 固定本轮实际安装的 Team 服务、worker 以及对应源码 SHA256。`patch-baseline.mjs` 校验全部输入后，生成语义相同的源码扩展和部署 JS 扩展；不接受其他版本，也不覆盖原始输入。

```sh
node integrations/harness-team/patch-baseline.mjs BASELINE_DIRECTORY OUTPUT_DIRECTORY
node --check OUTPUT_DIRECTORY/artifacts/team-server-index.js
node --check OUTPUT_DIRECTORY/artifacts/team-runtime-index.js
```

`OUTPUT_DIRECTORY/sources/` 对应 Team 仓库的源码路径；`artifacts/team-server-index.js` 覆盖新网关镜像内 `@deepseek-ai/dsh-host-team-server/lib/index.js`，`artifacts/team-runtime-index.js` 覆盖新 worker 镜像内 `@deepseek-ai/dsh-host-team-runtime/lib/index.js`，两镜像均包含 `addon/` 包。不得只改正在运行的容器文件。镜像、源码、基线和 `patch-manifest.json` 共同归档，后续构建从相同输入重放；升级 Team 时必须重新审查接口并固定新输入。

## 保留旧会话的迁移

`migrate.mjs` 仅在部署服务器运行，先以 SQLite 在线备份保存原始模型库的一致快照，再从私密临时副本读取加密目录，通过 Switch 管理接口复制凭证。原 Team 模型库始终只读，身份库、用户 home 与 workspace 数据卷均不修改。目标 Switch 必须为空；复制前预检总模型数不超过 100、上下文不超过 2000000 Token。迁移不发起模型请求。

迁移文件记录旧 `serviceId/modelId`、新 `connectionId/revision` 及非敏感显示信息。新连接初始版本为 1；迁移前已启用的旧会话选择继续有效，原禁用服务保持不可用。编辑 Switch 连接会提高版本并撤销旧会话别名，目录提供新的固定版本供重新选择；不会把旧会话静默指向新配置。删除连接同样撤销别名。

迁移环境：`TEAM_DATA_DIR`、`TEAM_ENCRYPTION_KEY_FILE`、`TEAM_SWITCH_ADMIN_URL`、`TEAM_SWITCH_ADMIN_TOKEN_FILE`、`TEAM_SWITCH_ALIASES_FILE`、已创建的 `TEAM_SWITCH_MIGRATION_BACKUP_DIR`，以及明确的 `TEAM_SWITCH_MIGRATION_CONFIRM=copy-catalog-to-empty-switch`。管理凭证只在迁移容器中临时挂载，常驻 Team 网关只挂载推理凭证。工具不会输出原 API Key、管理/推理凭证或原供应商地址。

失败时原目录和会话仍保留；不要在已经部分导入的目标上自动重跑。先保留报告及两端快照，恢复独立 Switch 目标到导入前状态，再人工重试。不得把数据库、凭证和运行数据加入 Docker 镜像或离线镜像归档。

## 切换与回滚

切换前通过已安装 worker 的 `inspect-worker-idle.mjs` 读取实时 `session/list` 的 `running` 状态；读取失败不视为空闲。worker 根文件系统只读时，通过 `docker exec -i WORKER node --input-type=module < inspect-worker-idle.mjs` 运行，不复制到容器文件系统。该脚本只输出会话数量和运行数量，临时观察连接结束后释放。

Team 的容器管理器不会自动替换已存在的旧 worker。保留全部 home/workspace 数据卷、用户 ID 及原 worker 签名密钥；确认空闲后停止并重命名旧容器为回滚副本，新镜像用原名称和原数据卷重新启动。不得使用 `docker rm -v`。回滚时停止新容器、恢复旧名称与旧 compose 镜像，保持原数据库和密钥，之后核对账号、会话及原默认模型。

## 验证

```sh
node --test integrations/harness-team/tests/catalog.test.mjs
```

行为测试覆盖真实 HTTP 三协议字节流、缓存原子提交、旧会话 ID、版本撤销、不可用目录关闭、恶意路由地址、响应大小、凭证权限和不歧义的用户能力鉴权。`verify-installed.mjs` 在真实部署镜像内启动临时 `dsh --profile team`，使用独立空 Switch，检查原生工具流、已安装 Team runtime/Pi 适配器的工具与用量解析、取消、版本撤销及账号/浏览器会话重启保留。只有执行后的通过报告才证明实机链路通过；脚本存在及构建成功不能替代实机验收。

测试容器应设置 `--cpus 2 --pids-limit 512`，不挂载真实 Docker socket；本轮镜像所在宿主的 tmpfs 默认为 `noexec`，仅测试容器的 `/tmp` 须显式设置 `rw,exec,nosuid,nodev`，以运行测试拥有的 Docker preflight shim。普通部署容器不需要放宽 tmpfs 执行权限。

实机验收要求 `TEAM_ACCEPTANCE_ISOLATED=1`、`TEAM_SWITCH_ADMIN_URL`、临时管理和推理凭证文件、`TEAM_SWITCH_URL`、`TEAM_ACCEPTANCE_FIXTURE_HOST` 及 `TEAM_ACCEPTANCE_OUTPUT`。fixture 主机名必须是测试容器在隔离 Docker 网络中的名字；对外 API、付费模型及生产数据均不参与。
