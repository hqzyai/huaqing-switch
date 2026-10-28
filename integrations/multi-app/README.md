# 文生模型统一接入

范围为服务器上 Knowledge、Harness、Design、Construction、Sculpture 的现有文本生成调用，仅使用已有 OpenAI Chat、Responses、Anthropic 协议。Construction 的既有 Chat/VLM 图片输入经用户确认纳入；专用 YOLO、Embedding、重排、图片/视频/音频生成和 3D 任务不迁移，也不新增协议。

应用保存稳定地址 `http://<LAN>:28001/model-api/hq/services/<connection-id>/v1` 和独立于管理凭证的机器推理凭证。URL 固定中央连接 ID，Switch 对每个请求读取完整连接快照并注入当时的模型和供应商密钥。调用方的原有 model 字段作为非空标签保留；修改 Switch 的模型、地址或密钥后，后续请求读取新配置。协议改变或连接删除明确失败，不退回供应商直连、不自动重发计费请求。原 Harness 的固定版本目录、旧别名与会话选择不变。

| 应用 | 持久接入方式 | 保留范围 |
| --- | --- | --- |
| Knowledge | 新增聊天实例，只重新关联原聊天模型 ID | 原 Embedding 实例、向量、默认和工作流引用不变；默认复用已有 DeepSeek V4.1 Flash |
| Harness | 继续已有 Team 目录与 worker 适配器 | 原连接版本、身份、会话和数据卷不变 |
| Design | 原 Pi 模型项覆盖独立 baseURL，保持 provider/model ID | 原媒体文件与会话 ID 不变 |
| Sculpture | 统一聊天 helper 读取独立路由映射和机器凭证 | 原 DashScope 媒体、Tripo、上传和业务数据不变 |
| Construction | 同时更新 VLM 环境文件和活跃 provider 源，仅重建 wvp | YOLO、detector、流媒体、数据库和全部数据挂载保留 |

各子目录包含固定基线、可复现补丁、迁移前保护条件和回滚步骤。真实密钥仅在服务器读取、备份与迁移，不进入 Git、Docker 层或离线镜像包。配置导入不得把已经指向 Switch 的应用地址当作上游再次导入。模型业务数据使用一致快照，修改前后核对保护指纹；不得用 `down -v` 或空目录替换业务挂载。

## 网络和验证

Switch 继续运行在专用 rootless daemon，其余四项应用使用 rootful daemon。先运行 `network-policy.py` 的只读计划核对实际网桥，再由部署管理员选择调用应用执行 `--apply --applications ...`。脚本仅允许指定接口及 `/24` 应用网段访问已有 LAN `28001/tcp`，保留原有规则及私密备份，不开放公网新端口。Docker 网络被重建后应重新核验并运行此步骤。

验收须从真实应用容器发起，而非仅在宿主访问健康页。先以隔离上游验证三协议、流式、预算、鉴权与配置更新，再进行最小文本请求并检查 Switch 用量记录。修改 provider URL 前先验证受控路由；切换时确认没有活动模型任务，保留版本和回滚快照。非文本配置以字节/数据库指纹核对，避免共享密钥或共享实例被误改。
