# Design / Sculpture 文本模型接入

此目录只改现有 OpenAI Chat 文本调用，复用 Switch 已有协议；图像、视频、音频、3D 及其原供应商凭证均保持原样。补丁仅针对 `baseline-sha256.json` 记录的两份 Linux amd64 镜像和实际部署文件。没有运行中容器文件修改，也不将凭证或业务状态复制到新镜像。

## 可重放镜像

从对应基线镜像取出清单中列出的公开源码，分别保存为 `design-baseline/` 和 `sculpture-baseline/`；不要提取 `.env`、实际 YAML/JSON 配置、数据库或用户目录。构建脚本先验证本地镜像身份，再逐文件校验源码 SHA256，输出目录必须不存在。任一基线不匹配都应停止，升级上游后重新审查。

```sh
export DOCKER_HOST=unix:///var/run/docker.sock
python3 integrations/multi-app/design-sculpture/build-overlay.py design \
  design-baseline design-overlay --tag huaqing-design-switch:<commit> \
  --source-commit <full-git-sha>
python3 integrations/multi-app/design-sculpture/build-overlay.py sculpture \
  sculpture-baseline sculpture-overlay --tag huaqing-sculpture-switch:<commit> \
  --source-commit <full-git-sha>
```

Dockerfile 只复制少量静态源码，保留原镜像运行用户、入口、依赖和业务文件。最终镜像标签、源码 SHA、基线身份、补丁输出清单和隔离测试结果应一起记录；不能把临时候选镜像的工作树标签当作已提交源码的发布来源。

## Design

已部署 Pi 适配器 `@deepseek-ai/dsh-llm-pi-ai` 0.1.1-rc.2 只允许 provider 级 baseURL。本补丁给既有 `models` 项增加可选 `baseURL`，解析时优先于 provider 默认值。原 `dashscope/qwen-plus`、`dashscope/qwen3.8-flash` 标识、模型参数和会话引用保留，不新增协议。

在持久目录 `/app/.od/sandbox/agent-home/.dsh/` 中，`settings.yaml` 的 `llm-pi-ai.providers.dashscope.models` 两项分别填写各自稳定 Switch URL：

```yaml
llm-pi-ai:
  providers:
    dashscope:
      api: openai-completions
      apiKeyEnv: HQ_SWITCH_CHAT_API_KEY
      baseURL: http://<switch-lan-ip>:28001/model-api/hq/services/unconfigured/v1
      models:
        - id: qwen-plus
          baseURL: http://<switch-lan-ip>:28001/model-api/hq/services/<qwen-plus-connection>/v1
        - id: qwen3.8-flash
          baseURL: http://<switch-lan-ip>:28001/model-api/hq/services/<qwen-flash-connection>/v1
```

修改时保留实际配置其余字段；示例不是整文件覆盖模板。provider 默认 URL 留在明确拒绝的 Switch 路径，使漏配的新模型失败，不能继续直连供应商。原 `.credentials.yaml` 中的 chat 供应商凭证先由服务器内迁移程序导入 Switch，再新增独立 `refs.HQ_SWITCH_CHAT_API_KEY` 保存机器推理凭证，provider 的 `apiKeyEnv` 只改指新 ref。原 `refs.DASHSCOPE_API_KEY` 保留，避免未知非文本消费者受影响。真实值不得进入源码、命令行或日志。独立的 `/app/.od/media-config.json` 及原媒体环境完全不动。

## Sculpture

`dashscope_chat.py` 是现有 Chat 调用公共入口。补丁让所有六个调用模块显式传模型标识，并从独立 chat helper 读取 URL / 推理凭证。新增配置仅为两个只读文件挂载：

| 环境变量 | 内容 |
| --- | --- |
| `HQ_SWITCH_CHAT_ROUTES_FILE` | 绝对路径，非敏感模型到 Switch URL 的 JSON 文件 |
| `HQ_SWITCH_CHAT_TOKEN_FILE` | 绝对路径，独立机器推理凭证文件，0600、普通文件、无符号链接 |

路由格式：

```json
{
  "schemaVersion": 1,
  "models": {
    "qwen3.8-flash": "http://<switch-lan-ip>:28001/model-api/hq/services/<qwen-connection>/v1",
    "deepseek-v4-flash": "http://<switch-lan-ip>:28001/model-api/hq/services/<deepseek-connection>/v1"
  }
}
```

实际 URL 必须使用私网 IPv4 或 loopback 地址、明确端口和固定 Switch 路径；不能带凭证、查询或 fragment。两个变量成对配置；启用后缺路由、凭证无效、重复 JSON key、文件权限错误均明确失败，不回退原 DashScope API Key。全部未配置时保留旧部署行为，供未迁移环境及回滚使用。

原 `DASHSCOPE_API_KEY`、`dashscope_base_url`、`dashscope_video_base_url` 仍供媒体使用；不能改为 Switch 文本 URL 或推理凭证。新的两个文件仅在容器运行时只读挂载，不放入镜像。模型名称作为稳定客户端别名，真正上游模型和供应商凭证由选定 Switch connection 管理。

## 验证和切换

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s integrations/multi-app/design-sculpture/tests -p 'test_*.py' -v
node --test integrations/multi-app/design-sculpture/tests/test_design_state_atomic.mjs
node --experimental-vm-modules --experimental-import-meta-resolve \
  integrations/multi-app/design-sculpture/tests/verify-design.mjs
```

Design 测试必须在候选 Design 镜像中运行，读取实际安装模块，通过隔离 VM 仅向测试暴露原有纯解析函数；测试真实 Pi adapter 流式调用、两个模型的独立 HTTP 路由、原 ID 保留及未配置模型拒绝。Sculpture 测试可用标准库在本机运行；镜像验收时设置 `SCULPTURE_SWITCH_MODULE_PATH=/srv/quyang-ai/app/services/providers/switch_chat.py`，测试安装后的实际模块。

测试容器使用 `--network none`（仅 loopback）、只读根文件系统、独立 `/tmp` 和假凭证，不挂载生产配置、数据库或 Docker socket。检查实际部署容器 ID / 启动时间未变，保存报告后删除测试容器。

正式切换前先核对两个应用容器到 Switch LAN 入口的可达性。跨 rootful / rootless Docker 的请求可能被宿主防火墙阻断，只能按实际应用 bridge / 源网段精确放行，不能为此开放公网或关闭防火墙。使用 SQLite 在线备份和一致的私密配置快照保留用户、会话及媒体凭证；检查真实文本请求空闲后再更新镜像。持久任务状态需按类型与更新时间核实，历史 `running` 行不等于当前外部模型请求。任何未通过的链路均不能宣称迁移成功。

## 服务器内迁移操作

`migration_common.py`、`register.py`、`migrate.py`、`design-state.mjs` 和 `preflight.py` 仅在服务器执行。它们从正在运行的客户端读取实际文本配置和凭证，私密值留在服务器进程、0600 文件和 0700 目录中。不能将私密计划、原始 Compose、`.env`、凭证备份或数据库下载到工作站。公开验收 JSON 只含模型标识、镜像身份、指纹和通过状态。

1. `python3 register.py` 在所有应用共用的 `state/private/multi-app-import.lock` 下进行 CAS 导入；只复用协议、URL、模型、容量、图像能力和凭证完全相同的连接。保留原活动连接、旧模型 revision、客户端配置及媒体密钥。注册日志可安全重跑；既有回执验证成功后不再次导入已经迁走的来源。
2. `python3 migrate.py prepare` 生成私密候选和回滚计划，使用在线 SQLite 备份保存 Design 数据库，校验会话绑定与实际任务状态。它不替换现用配置，也不重建生产容器。现用 Compose 必须和容器一致；唯一已核实例外是 Sculpture 的 `DEMO_PASSWORD` 既有漂移，脚本只在候选中保留 live 值，并要求保留值后的 Compose hash 精确等于原容器 label，防止升级或回滚意外轮换登录密码。`.env` 不改，原源文件仍完整备份。原 Compose 文件同样保持字节不变，新增 override 只保存在服务器 0700 目录的 0600 文件中，禁止将密码写入普通可读 Compose。
3. `python3 preflight.py` 从两个真实容器分别调用四个稳定 Chat 入口，每个模型一次、最多 64 输出 token，只保存 HTTP 状态、非空文本布尔和用量。失败回执不会自动重试；失败禁止切换，需先审查。
4. 审阅准备及真实调用回执，明确批准后运行 `python3 migrate.py apply --plan <server-private-plan> --confirm switch-text-only`。脚本再次验证容器身份、所有源文件指纹和任务空闲状态，只更新已审查镜像和独立 chat 配置。候选和回滚 override 均固定实际 Docker image ID，保留可读标签供报告核对，启动使用 `--pull never`，避免等待期间标签变化导致运行其他版本。正式调用在原文件列表后追加私密持久 override，精确文件列表写入 `state/private/design-sculpture-compose-authority.json` 并体现在容器 Compose labels；以后重建必须沿用该权威列表，不能只运行原始 Compose。原媒体文件、原 ref、所有原环境值和既有卷来源均须保持；Design 先停止目标服务再原子替换 chat 文件，重建只涉及目标服务且不重新构建镜像。
5. 失败自动回滚已经触及的服务。手动恢复使用 `python3 migrate.py rollback --plan <server-private-plan> --confirm switch-text-only`。它先核对未被人工修改的文件，再恢复原镜像与配置；Sculpture 保留原 live 登录值。独立 chat 文件只有仍匹配该计划时才清理。共享 Switch 已注册连接和应用数据库不会自动回退，避免覆盖迁移后新增的用户数据；一致数据库备份保留供人工灾难恢复使用。

多步操作写入私密 journal；命令中断后先检查计划 `phase` / `touched`，再按同一计划回滚，不能重新生成计划掩盖中间状态。所有正式镜像必须使用已提交源码的完整 Git SHA；操作脚本更新不改变两个 overlay 的已验证源码字节。

`prepare`、`apply`、`rollback` 和注册共用服务器的导入锁。配置写入使用独立随机临时名，失败只清理本次临时文件；Sculpture chat 目录尚未写完时，回滚允许清理已知文件的子集，逐个验证内容后再删除，未知或改写文件仍会阻止清理。更改准备计划格式后，未应用的旧计划必须标记作废并重新准备；原调用回执要保留，不能借重新准备盲目重复计费请求。

Sculpture 保留 live `DEMO_PASSWORD` 的 override 会优先于 `.env`。将来若要主动应用 `.env` 中的新登录密码，必须先审查并修改或移除此保留项；不可把 `.env` 改动误当成已经生效。私密 Compose 目录仍被运行中容器引用时必须保留，不能作为普通旧备份清理。
