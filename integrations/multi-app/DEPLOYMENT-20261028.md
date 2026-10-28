# 192.168.2.11 文生接入验收记录

核验日期：2026-10-28。Switch 运行源码为 `4e173995b162b5468a0129fd5d20587e2a9fe872`，入口为 `http://192.168.2.11:28001`，部署根目录为 `/home/ubuntu/Workspace/huaqing-switch`。此记录只证明表中注明的验收范围。

| 应用 | 当前状态 | 证据和限制 |
| --- | --- | --- |
| Knowledge :28002 | 文本已切换；Embedding 已独立修复 | 文本默认使用 Switch 的 `deepseek-v4.1-flash`；Embedding 按后续授权直连指定 Maas 的 `qwen3.7-text-embedding`，实测 1024 维，原模型 ID 和知识库引用保留；无需重启 |
| Harness :28003 | 继续使用 Switch | 实际目录 9 服务 / 9 模型，9 路由可解析；原默认连接 revision 1、旧别名和两个会话指纹保留；未重启 worker，也未重复调用模型 |
| Construction :28006 | 现有 Chat/VLM 已切换 | `qwen3-vl-plus` 不变；合成文本返回 200，统计 13 输入 / 3 输出 token；合成图片正确识别红色方块，统计 86 / 3；仅重建 wvp，2298 个原历史文件及持久配置指纹保持，YOLO 与其他容器保持 |
| Design :28005 | 已修复并切换 | 按用户选择，用该应用自身已有的有效 DashScope 凭据修复两个文本连接；原 Qwen 名称、模型 ID、媒体配置和旧凭据 ref 保留；两个真实文本请求均为 200，分别统计 13 / 1 和 30 / 1 token |
| Sculpture :28007 | 已修复并切换 | 原 DeepSeek 客户端别名保留，中央上游改为既有 `deepseek-v4.1-flash`；Qwen 合成图片识别通过，统计 104 / 2；实际已安装 helper 的 JSON 结构化响应通过，统计 14 / 6；媒体环境、任务与会话保留 |

Knowledge 原聊天池的三个备选已独立导入 Switch，未重新建立负载均衡池，也未替换指定默认模型；这三个备选没有逐一发送生成请求。后续用户授权修复 Embedding：替换不可达的容器 localhost 地址及代理专用旧别名，直连用户指定的 Maas API，继续使用原路由同型号 `qwen3.7-text-embedding`。按明确授权抽取 5 份文档的标题及正文共 10 条输入，按原清理、截断和标题权重重建，最低余弦相似度为 0.999997；原 104 份文档、828 条向量、索引映射与 sequence 保持不变。实际已配置 factory 返回有效 1024 维向量，复用该合成向量只读 kNN 命中 3 条，未增加外发业务文本。完整 RAG 问答质量未验收，4 个历史解析失败任务没有自动重跑。细节见 `knowledge/README-embedding-configuration.md`。

Embedding 继续由 Knowledge 直接调用，不经过 Switch，不新增 Switch 协议。Construction 和 Sculpture 的图片输入验收仅使用合成 PNG，没有发送摄像头或业务图片，也未执行生图、视频、3D 或 YOLO 任务。

Design / Sculpture 的原失效证据完整归档。修复后只重试原失败的三个文本请求，原成功 Qwen 文本回执直接复用；另分别执行一项 Qwen 合成图片验收和一项 DeepSeek 实际 helper JSON 验收。每次先持久保留 attempt，全部零重试。Sculpture Qwen 的图片能力验证通过后才将对应 Switch 卡的 `supportsImages` 改为 true；默认 DeepSeek 连接仍为 revision 1，原 Harness 会话绑定不变。

两应用只替换目标服务镜像和独立 Chat 配置。候选与回滚均固定实际 image ID，禁止临时拉取；Design 停服务前及写入前核对原配置、项目与会话标记，回滚只恢复已知 Chat 文件，保留新增会话与当前非文本配置。最终私密计划及 Compose 权威文件列表保存在服务器，不打入镜像或公开配置归档。

Switch 的 HTTP 管理界面已核对 9 个配置、用量页、编辑取消、登录退出与会话隔离，未修改模型配置或发送付费请求。Harness 和 Design 的匿名根页为 401，属于已响应的认证入口；不能把它当作登录或业务流程验收成功。其他三个匿名根页为 200。

已部署 Switch 版本的 [主 CI](https://github.com/hqzyai/huaqing-switch/actions/runs/40630929424) 和 [Linux Server CI](https://github.com/hqzyai/huaqing-switch/actions/runs/40630929432) 通过。另有 28 项隔离整栈验收及 9 项生产升级保护检查通过。所有生成请求均为短合成文本、最多 64 输出 token，不重试失败请求；脱敏报告不含消息正文或凭据。

本次 Switch / Harness 五镜像离线归档已校验 gzip、tar、重新导入及镜像 ID：

```text
/home/ubuntu/Workspace/images/huaqing-switch-linux-x86-e70fab39ed80b60ee3b2c3afb76ef67a567c357150265865fa470dc382f6b30f-20261028130314.tar.gz
```

归档 SHA256 与文件名相同，大小 611642788 字节；不包含数据库、凭据或业务卷。Linux 副本保存在 `/home/ubuntu/Workspace/packages/`，文件名前缀为 `huaqing-desktop-linux-x86-`。Design / Sculpture 的 `4e17399` overlay 镜像已在服务器 rootful daemon 上线；本五镜像归档不含这两份应用 overlay，重建它们使用本目录固定基线与补丁工具。

脱敏验收 JSON 位于服务器部署根目录的 `acceptance/`。配置与数据库备份留在服务器私密目录中，不能随公开配置包或镜像交付。
