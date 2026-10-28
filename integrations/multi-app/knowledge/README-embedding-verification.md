# Knowledge Embedding 回归检查

`verify-installed-embedding.py` 只读取已安装 Knowledge 的原 Embedding 模型 ID，并通过实际 `get_model_config_by_id` / `TenantLLMService.model_instance` 构建 `OpenAI_APIEmbed`。它不修改模型、默认值、知识库或索引，不接受 API Key 参数。凭证只从服务器当前实例读取，始终留在容器内。

公开脚本安装位置：`/home/ubuntu/Workspace/huaqing-switch/ops/multi-app/knowledge/verify-installed-embedding.py`。必须在部署服务器运行，且不能启用 Python 优化模式。

## 只读 factory 检查

```sh
python3 verify-installed-embedding.py factory \
  --expected-base-url https://ws-8ydu3kv5yatgl3a2.cn-beijing.maas.aliyuncs.com/compatible-mode/v1 \
  --expected-model qwen3.7-text-embedding \
  --report-file /home/ubuntu/Workspace/huaqing-switch/acceptance/knowledge-embedding-factory.json
```

`factory` 不发送模型请求。修改配置后，要明确传入期望的新地址与模型名，防止误验其他连接。

## 单次合成编码

以下旧地址基线已执行一次，返回 `EmbeddingError` / `APIConnectionError`，退出码 `1`；对应脱敏回执保存于服务器 `acceptance/knowledge-embedding-red-baseline.json`。不要重复执行失败基线。

```sh
python3 verify-installed-embedding.py encode --allow-model-call \
  --expected-base-url http://127.0.0.1:14001/v1 \
  --expected-model ragflow-embedding \
  --report-file /home/ubuntu/Workspace/huaqing-switch/acceptance/knowledge-embedding-red-baseline.json
```

新供应商编码仅在明确授权真实模型调用、且当前模型配置已受控修改后执行：

```sh
python3 verify-installed-embedding.py encode --allow-model-call --allow-remote-provider --check-index \
  --expected-base-url https://ws-8ydu3kv5yatgl3a2.cn-beijing.maas.aliyuncs.com/compatible-mode/v1 \
  --expected-model qwen3.7-text-embedding --expected-dimension 1024 \
  --report-file /home/ubuntu/Workspace/huaqing-switch/acceptance/knowledge-embedding-green.json
```

脚本只允许原 localhost 和上述已授权新地址；不能向旧第三方端点发远程编码。每次只编码一个固定合成文本，SDK 重试为零，禁止重定向。原 localhost 的 HTTP 各阶段超时为 5 秒、连接阶段为 2 秒；远程 HTTP 各阶段为 30 秒、连接阶段为 5 秒。外层进程上限为 45 秒，启用索引读取时为 120 秒。请求钩子核对方法、地址、模型、输入和当前实例凭证，最多允许一次请求。远程回执绑定当前供应商密钥的 HMAC 指纹。回执只记录状态、维度、异常类型和计数，不输出正文、向量、错误响应或凭证。

可选 `--check-index` 在同容器内复用本次合成编码的向量，对原 `q_1024_vec` 索引执行 `k=3`、`num_candidates=20` 的只读 kNN，过滤现有两个知识库，并设置 `_source=false`。它不再调用供应商、不读取业务正文、不重排或生成回答；结果名为 `syntheticQueryIndexKnnSmoke`，只报告命中数、有限分数和标识哈希，并核对 mapping、828 条计数和主分片 sequence 不变。这只证明合成向量可读索引，不证明业务语义相关性或完整 RAG 聊天质量。

编码前以 `O_EXCL` 创建 `<report-file>.attempt.json`。同一成功回执再次执行会直接复用，新增调用为零。失败、不匹配或仅有 attempt 的回执都拒绝自动重试；先审查服务器状态，不能更换回执路径来绕过单次调用约束。`factory` 不需要调用锁。

## 已有向量的兼容边界

盘点时 Elasticsearch 有 828 条 `q_1024_vec` 向量；原 AgentOS 当前路由配置使用 `qwen3.7-text-embedding`，但索引未存历史生产模型信息。同名、同维度或一次合成编码成功均不足以证明旧向量兼容。

原文档管线使用正文清理、Token 截断和标题向量加权，默认结果为 `0.1 * title_vector + 0.9 * body_vector`。复验必须在服务器内按实际输入规则重建抽样向量并比较，正文与向量不可下载。此脚本的合成检查不承担抽样兼容验证，也不授权批量重建或删除索引。

原聊天迁移工具的保护指纹包含 Embedding 模型和原实例；本次授权修改 Embedding 后，旧聊天快照校验会按设计拒绝。保留原验收证据，并使用本次受控修复的独立前后保护报告；不要放松聊天工具的旧指纹约束。
