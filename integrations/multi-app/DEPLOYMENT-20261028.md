# 192.168.2.11 文生接入验收记录

核验日期：2026-10-28。Switch 运行源码为 `4e173995b162b5468a0129fd5d20587e2a9fe872`，入口为 `http://192.168.2.11:28001`，部署根目录为 `/home/ubuntu/Workspace/huaqing-switch`。此记录只证明表中注明的验收范围。

| 应用 | 当前状态 | 证据和限制 |
| --- | --- | --- |
| Knowledge :28002 | 文本已切换 | 原 `ragflow-chat` ID 保留，默认使用既有 `deepseek-v4.1-flash`；已安装适配器的一次请求返回 200，统计 53 输入 / 12 输出 token，匹配 Switch 持久记录；无需重启 |
| Harness :28003 | 继续使用 Switch | 实际目录 9 服务 / 9 模型，9 路由可解析；原默认连接 revision 1、旧别名和两个会话指纹保留；未重启 worker，也未重复调用模型 |
| Construction :28006 | 现有 Chat/VLM 已切换 | `qwen3-vl-plus` 不变；容器内一次合成文本请求返回 200，统计 13 输入 / 3 输出 token；仅重建 wvp，2298 个原历史文件及持久配置指纹保持，YOLO 配置与其他容器保持 |
| Design :28005 | 已准备，尚未切换 | 原两个文本模型的供应商凭据均返回 401；等待用户选择是否使用该应用现有有效 DashScope 凭据修复文本配置 |
| Sculpture :28007 | 已准备，尚未切换 | Qwen 文本验证返回 200，统计 30 输入 / 1 输出 token；旧 `deepseek-v4-flash` 返回 404，等待用户选择是否改用已有 `deepseek-v4.1-flash` |

Knowledge 原聊天池的三个备选已独立导入 Switch，未重新建立负载均衡池，也未替换指定默认模型；这三个备选没有逐一发送生成请求。Embedding 保持原配置，其中 `127.0.0.1:14001` 在容器内不可达是迁移前已有问题；本次未修复或验收完整 RAG 检索。Construction 使用合成文本验证 Chat 链路，没有发送摄像头画面或执行图片推理。

Switch 的 HTTP 管理界面已核对 9 个配置、用量页、编辑取消、登录退出与会话隔离，未修改模型配置或发送付费请求。Harness 和 Design 的匿名根页为 401，属于已响应的认证入口；不能把它当作登录或业务流程验收成功。其他三个匿名根页为 200。

已部署 Switch 版本的 [主 CI](https://github.com/hqzyai/huaqing-switch/actions/runs/40630929424) 和 [Linux Server CI](https://github.com/hqzyai/huaqing-switch/actions/runs/40630929432) 通过。另有 28 项隔离整栈验收及 9 项生产升级保护检查通过。所有生成请求均为短合成文本、最多 64 输出 token，不重试失败请求；脱敏报告不含消息正文或凭据。

本次 Switch / Harness 五镜像离线归档已校验 gzip、tar、重新导入及镜像 ID：

```text
/home/ubuntu/Workspace/images/huaqing-switch-linux-x86-e70fab39ed80b60ee3b2c3afb76ef67a567c357150265865fa470dc382f6b30f-20261028130314.tar.gz
```

归档 SHA256 与文件名相同，大小 611642788 字节；不包含数据库、凭据或业务卷。Linux 副本保存在 `/home/ubuntu/Workspace/packages/`，文件名前缀为 `huaqing-desktop-linux-x86-`。Design / Sculpture overlay 镜像另存于服务器 rootful daemon，尚未上线；本五镜像归档不含这两份 overlay。

脱敏验收 JSON 位于服务器部署根目录的 `acceptance/`。配置与数据库备份留在服务器私密目录中，不能随公开配置包或镜像交付。
