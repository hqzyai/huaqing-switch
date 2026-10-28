# Construction VLM 迁移

本工具只迁移现有 `qwen3-vl-plus` 的 OpenAI Chat 调用。专用 YOLO PPE 检测、媒体、摄像头、数据库、账号和历史记录保持原配置。Harness 已通过原有适配器连接 Switch，不重跑其 empty-only 迁移器。

在服务器内先把原 VLM 配置导入 Switch，再使用返回的新连接 ID 准备迁移。`register.py` 与 `migrate.py` 放在同一目录：

```sh
python3 register.py
python3 migrate.py prepare --connection-id CONNECTION_UUID
python3 migrate.py apply --plan /home/ubuntu/Workspace/huaqing-construction/backups/switch-vlm-TIMESTAMP/plan.json
```

注册通过管理 API 的配置版本 CAS，只新增 `Construction · qwen3-vl-plus`（OpenAI Chat、contextWindow 32768、maxOutputTokens 2048、supportsImages=true），不选择连接、不修改原连接。凭证只从服务器 0600 原配置文件读取；重复运行优先识别已经切换的稳定 Switch 路由或私密注册回执，不把 Switch 地址和推理凭证再次当成供应商导入。发现同名注册时，只在服务器只读核对数据库中的原始配置和密钥是否完全匹配，再复用；不覆盖用户修改。原 provider 与环境文件副本只写服务器私密备份，注册本身不修改 Construction 活动配置、不重启应用、不进行推理。

`prepare` 不修改活动配置或容器；它使用服务器内的 0600 推理凭证，只读检查稳定的 `/model-api/hq/services/<id>/v1/models`，验证模型仍为 `qwen3-vl-plus`。备份及候选配置仅存于服务器 0700 目录、0600 文件，绝不下载到客户端或写入镜像。应用只持有 Switch 推理凭证，上游凭证由 Switch 管理。

实际应用配置是 `deployment/config/wvp.env`，并非顶层 Compose `.env`。工具同时切换其中的 `AI_ANALYSIS_API_URL` / `AI_ANALYSIS_API_KEY` 及活跃 `deployment/secrets/ai-provider.json`；`configure_ai.py` 今后重跑仍指向 Switch。`AI_ANALYSIS_MODEL` 保持不变，稳定连接 URL 由 Switch 读取该连接当前版本，不把版本号写死。

目录改名后，旧容器的 bind Source 仍显示不存在的旧 `Workspace/gb28181`。工具不会创建这些路径，而是逐个比较 canonical `huaqing-construction` 路径和 live mount 的 device / inode、访问模式及目标，确认仍是同一份真实数据；同时验证镜像、其余环境和服务基线。全部通过才允许切换。若背景 YOLO 任务定义存在，`apply` 拒绝执行，须等空闲窗口；没有热加载能力的 JVM 仅重建 `wvp`，显式 `--no-deps --no-build --pull never`，不重建 detector、媒体、MySQL、Redis 或前端。

两份配置使用原子文件替换，并保留私密备份与阶段日志；启动或验证失败会恢复两份原配置并只重启 `wvp`。计划一旦开始执行不可重复套用，失败后须检查阶段、私密日志和业务健康后重新准备。业务数据目录不移动、不删除，任务和历史文件不清理。

`apply` 的配置验收验证同镜像、同数据 inode、全部非 VLM 环境不变及其余容器 ID / 启动时间不变，不调用付费模型。完整交付还需由执行方做实际稳定路由、文本 / 工具流 / 用量检查。尤其应从 `wvp` 容器内部验证 Switch 网络可达，不能用宿主机 curl 成功代替；当前部署最初存在 Docker bridge 到 rootless Switch 入口被防火墙拦截的问题。
