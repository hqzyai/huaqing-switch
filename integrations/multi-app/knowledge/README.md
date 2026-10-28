# Knowledge 文生模型接入 Switch

此工具只迁移 `192.168.2.11:28002` 的现有 `ragflow-chat`。Embedding、重排、语音、图像以及知识库向量不在迁移范围内。所有操作在 `hqzyai-agentos-workstation-001` 的 `ubuntu` 账户执行；公开脚本可安装到 `/home/ubuntu/Workspace/huaqing-switch/ops/multi-app/knowledge/`，不包含密钥或数据库。迁移与备选导入分开执行、分别备份和回滚。

## 已核实的现状和目标

Knowledge 容器为 rootful Docker 的 `ragflow-workspace-ragflow-cpu-1`，配置在 `/ragflow/conf/service_conf.yaml`。模型使用 MySQL 的 `tenant_model_provider` / `tenant_model_instance` / `tenant_model`，旧 `tenant_llm` 为空。原 `agentos` 实例共用聊天和向量配置；改它的地址会误迁向量，所以新增 `huaqing-switch-text` 实例，只将原聊天模型的 `instance_id` 关联到新实例，保持聊天模型 ID、其他模型、原实例、租户默认值、知识库、对话和工作流记录不变。

当前实际引用均携带稳定模型 ID；没有只依赖旧聊天 composite 名称的引用。脚本再次检查这一条件，发现旧名称引用就停止。运行时按 ID 逐次从数据库读取实例配置，没有这条读取路径的进程缓存；已构建的在途模型对象保持原样，后续新请求读取新实例，不需要重启应用或 worker。不要使用公共 provider 更新接口做此拆分：该接口根据提交列表更新或删除同实例模型，遗漏向量模型会造成删除。

用户指定复用 Switch 现成的 `deepseek-v4.1-flash` 连接 `2d5b235b-1723-4d65-b23a-48e72e584ef9`。默认目标为：

```text
http://192.168.2.11:28001/model-api/hq/services/2d5b235b-1723-4d65-b23a-48e72e584ef9/v1
```

Switch 按路径选择此连接的当前版本，并注入实际上游模型与密钥。Knowledge 的模型名继续为 `ragflow-chat`，不需要把业务引用改成供应商模型名。目标需要主任务部署的稳定服务入口和容器网桥访问规则；本工具不开放端口、不改防火墙、不添加模型协议。

## 聊天实例拆分

在服务器设定脚本目录和一个新的私密备份文件路径：

```sh
cd /home/ubuntu/Workspace/huaqing-switch/ops/multi-app/knowledge
python3 migrate-chat.py plan
python3 migrate-chat.py prepare --backup-file /home/ubuntu/Workspace/huaqing-switch/backups/knowledge-chat-20261028/knowledge.json
```

`plan` 只读检查真实表和引用。`prepare` 也不更新业务数据库，只将原聊天记录、原实例及保护指纹写入服务器私密备份文件；目录必须 0700、文件 0600、归当前用户所有，并且不能覆盖已有快照。备份包含原服务凭证，不能复制到源码、日志、Mac、镜像或交付包。

主任务确认 Switch 稳定入口和 Knowledge 网桥可达后，再执行：

```sh
python3 migrate-chat.py apply --backup-file /home/ubuntu/Workspace/huaqing-switch/backups/knowledge-chat-20261028/knowledge.json
python3 migrate-chat.py verify --backup-file /home/ubuntu/Workspace/huaqing-switch/backups/knowledge-chat-20261028/knowledge.json
```

凭证默认从 `/home/ubuntu/Workspace/huaqing-switch/state/private/inference-token` 读取；文件必须是当前用户拥有的 0600 普通文件，不允许符号链接。凭证仅通过服务器进程的 stdin 进入 Knowledge 容器，不出现在命令行、环境变量或脱敏报告中。目标地址可以用 `--base-url` 指定，但只允许同一服务器 `28001` 的稳定模型服务路径；不能使用管理凭证、任意公网地址或带凭证的 URL。

`apply` 先复核私密备份，锁定原聊天模型及原实例，然后在同一 MySQL 事务中新建文本实例和迁移一条关联。后置检查失败时回滚两条写入。它保存私密操作回执，`verify` 核对聊天 ID、向量原实例、所有默认引用和业务记录的保护指纹；不发送模型请求。生效链路、流式响应与用量统计仍需主任务另行做真实端到端验收，不能以数据库检查代替。

作用域回滚命令：

```sh
python3 migrate-chat.py rollback --backup-file /home/ubuntu/Workspace/huaqing-switch/backups/knowledge-chat-20261028/knowledge.json
```

回滚只恢复原聊天模型的实例关联并删除本工具创建的实例，保留迁移后新增的文档和工作流编辑。新实例被编辑、被其他模型复用，或原实例配置被修改时会拒绝自动删除，需先审查；不覆盖整个数据库。若执行中断且回执仍是 `pending`，先检查当前模型关联与同名实例，不要盲目重复 `apply`。原实例的 `127.0.0.1:14001` 是迁移前已有配置，回滚仅恢复配置，不代表原网关在容器内可达；向量链路不在此次修复范围内。

## 独立导入三个文本备选

原 AgentOS `ragflow-chat` 有三个 Dashscope OpenAI-compatible 备选：`deepseek-v4-pro`、`deepseek-v4-flash-0731`、`qwen3.8-flash`，地址为 `https://maas.qianwenaiapi.com/compatible-mode/v1`。脚本在服务器内读取 `agentos-postgres` 的 `litellm` 数据库和 `agentos-ai-gateway` 的现有运行时密钥，解析 `LiteLLM_ProxyModelTable` / `LiteLLM_CredentialsTable`。原生协议仍是 Switch 已有的 `openai-chat`；不添加 Dashscope 专有协议，不导入 Embedding。

```sh
python3 agentos_source.py
python3 import-fallbacks.py plan
python3 import-fallbacks.py apply --backup-dir /home/ubuntu/Workspace/huaqing-switch/backups/knowledge-fallbacks-20261028
python3 import-fallbacks.py verify --backup-dir /home/ubuntu/Workspace/huaqing-switch/backups/knowledge-fallbacks-20261028
```

`apply` 使用非空 Switch 目录的管理 API 和全局 revision CAS 添加备选，先在上述新私密目录中做 SQLite 在线备份，再逐条记录操作回执。管理凭证仅从服务器 `state/private/admin-token` 读取，完整值不输出。原有连接的所有字段、私钥、connection revision、`activeId` 和 Harness 的已固定连接都不修改。复用完全相同的已有上游时不更新它；同模型不同密钥等差异会停止。遇到并发 CAS 冲突会保留私密部分进度，不能直接恢复整个 SQLite 备份覆盖其他人的改动。

新建的三条记录是独立备选，不恢复 AgentOS 的 `least-busy` 池，也不替换用户已选择的 `deepseek-v4.1-flash` 默认连接。原 128000 上下文和 8192 最大输出作为备选配置记录；真实上游私钥只写入 Switch，通过 JSON stdin 和服务器内存传递，不写入迁移回执或环境变量。

尚未有业务引用这些备选时，可作用域删除本次新建且未改动的记录：

```sh
python3 import-fallbacks.py rollback --backup-dir /home/ubuntu/Workspace/huaqing-switch/backups/knowledge-fallbacks-20261028
```

回滚不删除本来就有的复用记录，不覆盖其他现有连接；任何新增连接被选为 active 或被编辑时拒绝自动删除。如果其他应用已开始使用这些连接，先恢复它们的选择后再回滚。

## 可重放验证

```sh
python3 -m unittest discover -s integrations/multi-app/knowledge -p 'test_*.py' -v
```

测试只用合成凭证和内存 SQLite / 假管理 API，覆盖事务失败回滚、向量和引用保持、陈旧备份、旧名称拒绝、作用域回滚及后续用户编辑、非空目录 CAS、并发冲突、复用和密钥差异拒绝；不连接生产、不请求模型。生产预检和验收报告另外保存在 ignored `validation/multi-app-switch/knowledge/`。脚本拒绝 Python 优化模式，以免跳过事务安全断言。
