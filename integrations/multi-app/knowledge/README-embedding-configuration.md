# Knowledge Embedding 配置迁移

`configure-embedding.py` 是独立的服务器工具。源码需先完成审查和服务器 fixtures 验收，再执行迁移。本文和脚本本身不代表已经修改生产配置。

固定目标是 `https://ws-8ydu3kv5yatgl3a2.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`，真实模型名称为 `qwen3.7-text-embedding`。工具保留原 Embedding 模型 ID `86a86176c45a11f1abc8cd6d3e6ac241`、原实例 ID、所有知识库引用和已有索引；不会新增模型、修改 Switch、请求模型或重建向量。

只允许更新四个位置：原 `tenant_model.model_name`、原独占实例的 `api_key`、实例 JSON `extra.base_url`，以及经过显式审查的原租户 `embd_id`。租户旧复合名称必须精确等于 `ragflow-embedding@agentos@OpenAI-API-Compatible`，对应 `tenant_embd_id` 必须仍为原模型 ID；新复合名称仅替换模型名称部分。其他 JSON 配置和所有时间戳保留。

实际知识库的 `embd_id` 已直接保存原模型 ID，不需要修改。任何其他租户、知识库、对话、工作流或历史版本中的旧名称引用都提前拒绝；即使同一 DSL 还包含稳定模型 ID，也不会自动解释或重写。

## 安装和权限

以下文件必须一起部署在服务器的 `ops/multi-app/knowledge/`：

- `configure-embedding.py`
- `embedding_core.py`
- `embedding_evidence.py`

固定工作目录是 `/home/ubuntu/Workspace/huaqing-switch`；容器是 `ragflow-workspace-ragflow-cpu-1`。工具只通过 rootful Docker socket 进入容器，在容器内读取 `/ragflow/conf/service_conf.yaml` 的 MySQL 凭证。不要通过命令行传入 API Key，不要启用 Python `-O` / `PYTHONOPTIMIZE`。

新供应商密钥只从服务器 `state/private/knowledge-embedding-provider-token` 读取。凭据指纹使用同目录 `knowledge-embedding-attestation-salt` 的固定 32 字节私有随机盐和 HMAC-SHA256；两份文件均须属于当前用户、权限为 `0600`，且不能为符号链接。不要改写或重新生成已用于验收的盐。

备份路径必须位于 `backups/` 下的独立子目录，例如 `backups/knowledge-embedding-20261028/embedding.json`。从 `backups/` 开始的每层目录都必须属于当前用户、权限为 `0700`；工具只会以 `0700` 创建缺失目录。备份、pending journal、迁移回执和回滚标记均为 `0600`，文件使用排他创建和原子发布，拒绝覆盖已有路径或跟随符号链接。

`prepare`、`apply`、`recover-receipt`、`rollback` 共用 `state/private/multi-app-import.lock` 的排他 `flock`，被其他导入操作占用时直接拒绝。锁目录必须是真实目录并属于当前用户；现有锁文件必须是当前用户拥有的 `0600` 普通文件。

## 兼容性和凭据门禁

`prepare` 和 `apply` 都重新检查以下服务器文件，工具不会主动创建或补跑模型验收：

- `acceptance/knowledge-embedding-provider-batch.json` 及其 `.attempt.json`：单请求返回 16 个 1024 维向量。
- `acceptance/knowledge-embedding-provider-query.json` 及其 `.attempt.json`：单请求返回 1 个 1024 维向量。
- `acceptance/knowledge-embedding-provider-samples.json` 及其 `.attempt.json`：5 份不同文档按原处理规则形成 10 个输入，单请求返回 10 个 1024 维向量，每份旧新组合向量余弦相似度至少为 `0.9`。
- `acceptance/knowledge-embedding-credential-attestation.json`：绑定当前密钥的 HMAC 指纹、原 batch/query 成功回执和 attempt 的精确字节摘要，并证明密钥文件早于原请求，且与已核对的默认供应商凭据一致。

三次回执必须全部成功、HTTP 200、使用固定地址和模型、各自只有一次请求、向量有限且非零，并有正整数 Token 用量。回执与 attempt 的 ID、维度、数量必须对应。每次验收必须保留数据库保护摘要；samples 还须证明旧索引的 mapping、文档/向量计数和主分片 sequence 未变化。samples 回执和 attempt 必须直接绑定当前凭据指纹。

缺少 samples 成功回执、任意门禁为假或凭据不匹配时，不能 `prepare` 或 `apply`。不要以一次合成成功、相同维度或模型同名替代旧向量兼容性验证；5 份抽样通过也不宣称已经证明全部历史语料兼容。

私有备份保存原数据库快照、保护摘要、三份证据及 attempt 的字节摘要、attestation 摘要和目标凭据指纹，不保存新的明文 API Key。原数据库快照包含旧凭据，必须始终留在服务器私有备份中。公开输出只有固定模型标识、计数、摘要和布尔检查结果；异常仅输出类型，不输出数据库行、正文、向量、请求内容或凭据。

## 操作流程

在部署服务器进入工具目录后，先查看只读计划：

```sh
python3 configure-embedding.py plan --review-owner-tenant-legacy
```

`--review-owner-tenant-legacy` 明确允许在服务器内部识别原 provider 所属租户，并审查该租户唯一精确旧复合名称；租户 ID 不会出现在公开计划中。`plan` 没有业务写入或模型调用。它输出 `compatibilityReady`；兼容性证据缺失时该值为 `false`。

证据全部闭合后，创建一次性私有备份：

```sh
python3 configure-embedding.py prepare --review-owner-tenant-legacy \
  --backup-file /home/ubuntu/Workspace/huaqing-switch/backups/knowledge-embedding-20261028/embedding.json
```

完成具体备份和计划审查后，才执行：

```sh
python3 configure-embedding.py apply \
  --backup-file /home/ubuntu/Workspace/huaqing-switch/backups/knowledge-embedding-20261028/embedding.json
```

`apply` 拒绝已有 pending 或回执。它重新核对原快照、兼容性证据的精确字节摘要和当前密钥指纹，再排他创建 `<backup>.pending.json`。容器内核对全部受保护表均为 InnoDB，设置 SERIALIZABLE 隔离，在同一事务中锁定原模型、实例、实例引用和所属租户，核对基线后更新限定字段。出现任何异常或保护摘要差异时整笔回滚。

数据库提交后创建不可覆盖的 `<backup>.receipt.json`，再执行严格的只读验证：

```sh
python3 configure-embedding.py verify \
  --backup-file /home/ubuntu/Workspace/huaqing-switch/backups/knowledge-embedding-20261028/embedding.json
```

`verify` 不请求模型。它检查目标配置和原保护基线，后续正常业务写入也可能导致严格基线检查失败。不要通过重写备份、修改指纹或重复 `apply` 消除该差异。

## 回执丢失和回滚

如果数据库已经提交，但容器返回或服务器回执落盘失败，pending journal 会保留。已有 pending 时，`apply` 始终拒绝重试。先审查服务器状态，再显式执行只读恢复：

```sh
python3 configure-embedding.py recover-receipt \
  --backup-file /home/ubuntu/Workspace/huaqing-switch/backups/knowledge-embedding-20261028/embedding.json
```

恢复必须同时满足：pending 存在、成功回执不存在、原兼容性证据和当前密钥指纹仍匹配、完整原模型和实例的当前值精确等于本次预期目标、原 provider 未被编辑、租户所属引用字段仍匹配。恢复只创建本地私有回执，不执行配置更新；原配置仍未提交、目标后来被编辑或当前密钥不同都会拒绝。恢复保留最初保护基线，允许后来发生的无关业务写入，但不会把它们认可为原始基线。随后仍应执行 `verify` 并审查其结果。

需要撤销时使用同一私有备份及回执：

```sh
python3 configure-embedding.py rollback \
  --backup-file /home/ubuntu/Workspace/huaqing-switch/backups/knowledge-embedding-20261028/embedding.json
```

回滚只撤销本次拥有的模型名称、实例密钥/地址和租户精确复合名称，保留后来无关的租户资料、知识库、文档、对话或工作流写入。目标模型或实例、原 provider、所属租户引用被编辑，或者原实例后来被其他模型共享时拒绝回滚。回滚不要求重新请求模型或继续持有新供应商密钥，成功后另建 `<backup>.rolled-back.json` 标记。

pending、回执、回滚标记和任何不完整文件都不能通过删除或换一个备份路径来绕过操作历史。若回滚提交后标记写入失败，保留全部文件并进行服务器状态审查，不要盲目重试。

原聊天迁移工具及旧快照保护保持原样。本次 Embedding 配置变更会使旧聊天快照中的相关摘要发生预期差异，应使用本次独立保护报告审查，不能放松旧工具校验。

## 2026-10-28 实际验收

生产配置已完成一次原子切换，随后 6 项配置核对、29 项最终保护检查和 44 项服务器 fixtures 通过。历史实例名称 `agentos` 及 ID 保留以兼容引用，实际请求已经直连上述 Maas 地址，不经过 AgentOS 或 Switch。

新供应商的实际适配器测试为批量 16 条、查询 1 条和已明确授权的 5 文档标题加正文 10 条；全部返回有效的 1024 维向量，样本最低余弦相似度为 `0.9999973436261699`。已配置 factory 的单次合成编码再次返回 HTTP 200，并复用同一个向量完成只读索引 kNN，命中 3 条；没有额外模型请求、业务正文读取或重排/聊天生成。

原 104 份文档、828 条向量、索引映射与主分片 sequence，以及聊天配置、Switch 配置和原 Harness 会话均保留。没有重启容器、重建索引或自动重跑 4 个历史失败文档。完整 RAG 问答质量不在该合成索引检查的证明范围内。

本次新供应商验收共 4 个请求、8542 Token；业务内容仅在已授权的一次 10 条样本请求中发送到指定 API，没有下载正文、旧新向量或密钥到工作站。脱敏汇总位于服务器 `acceptance/knowledge-embedding-final.json`；原配置、私密备份、pending journal 和成功回执保留在 `backups/knowledge-embedding-20261028/`，不能进入 Git 或镜像。

模型接口和 1024 维参数参见[百炼同步 Embedding API](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)。实际应用仍保留原 8192 Token 配置和 16 条分批规则，以维持原输入处理；未因为供应商额度更大而修改已有文档管线。
