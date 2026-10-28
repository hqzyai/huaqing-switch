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
  -s integrations/multi-app/design-sculpture/tests -p 'test_sculpture.py' -v
node --experimental-vm-modules --experimental-import-meta-resolve \
  integrations/multi-app/design-sculpture/tests/verify-design.mjs
```

Design 测试必须在候选 Design 镜像中运行，读取实际安装模块，通过隔离 VM 仅向测试暴露原有纯解析函数；测试真实 Pi adapter 流式调用、两个模型的独立 HTTP 路由、原 ID 保留及未配置模型拒绝。Sculpture 测试可用标准库在本机运行；镜像验收时设置 `SCULPTURE_SWITCH_MODULE_PATH=/srv/quyang-ai/app/services/providers/switch_chat.py`，测试安装后的实际模块。

测试容器使用 `--network none`（仅 loopback）、只读根文件系统、独立 `/tmp` 和假凭证，不挂载生产配置、数据库或 Docker socket。检查实际部署容器 ID / 启动时间未变，保存报告后删除测试容器。

正式切换前先核对两个应用容器到 Switch LAN 入口的可达性。跨 rootful / rootless Docker 的请求可能被宿主防火墙阻断，只能按实际应用 bridge / 源网段精确放行，不能为此开放公网或关闭防火墙。使用 SQLite 在线备份和一致的私密配置快照保留用户、会话及媒体凭证；检查真实文本请求空闲后再更新镜像。持久任务状态需按类型与更新时间核实，历史 `running` 行不等于当前外部模型请求。任何未通过的链路均不能宣称迁移成功。
