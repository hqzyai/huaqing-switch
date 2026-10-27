# HQ-only 界面与启动策略

> 历史阶段记录：以下验收描述的是仅管理连接配置的上一阶段。当前产品名已改为 **Huaqing Switch**，本地服务与 HQHarness 接入已经实现；当前行为和交付包以 [接入说明](huaqing-harness-integration.md) 为准。

> 2026-10-27 后续需求：0.1.1 恢复独立 HQ 用量统计入口，并在模型限制中标注 Token 单位。统计范围和数据含义见[限制与用量说明](hq-usage.md)；以下“用量隐藏”描述仅属于早期阶段。

2026-10-22。用户要求隐藏路由、聚合和其他入口，只显示 HQ Harness。本次作为品牌/UI 迁移后的后续改动；本文件中的当前状态优先于前一阶段台账。

## 已实现

- 发行入口 `src/main.tsx` 挂载 `HqApp`，不挂载上游多客户端 `App`。导航仅为 **HQ Harness / 设置**；设置仅保留外观和产品来源信息。
- 路由、聚合、所有其他客户端，以及 MCP、Skills、提示词、会话、授权中心、用量统计、应用管理等入口均不渲染；没有设置开关可重新打开它们。
- HQ 页面不读取旧客户端/页面导航记录，历史 `visibleApps` 和 `last-app` 不能恢复隐藏入口。原有功能代码和数据保留，没有删除用户数据。
- 原生托盘仅包含 **HQ Harness / 退出 HQ Model Switch**，不创建原版托盘面板。
- `brand.config.json` 固定 `clientMode: hq-only`；资产准备脚本验证此策略。数据库初始化后进入独立 HQ setup，跳过旧 AppState、客户端配置恢复、供应商/Skills/MCP/提示词导入、授权管理器、旧路由/聚合恢复、会话扫描及原有后台任务；旧深链导入也不处理。HQ 退出不执行旧客户端 detach。
- HQ 连接可保存、编辑、设为默认、删除。数据单独保存在本产品 SQLite 的 `settings["hq.connections.v1"]`，没有借用 Claude/Codex 的供应商桶，也没有伪造 `AppType::Hq` 的协议能力。
- 保存使用单事务和 revision 比较，拒绝过期写入；失败保留表单草稿。删除前确认，删除默认项后清空默认项，不自动选择其他供应商。
- 列表返回凭证是否存在，不返回 API Key；编辑留空保留原密钥，显式勾选可清除。URL/模型/名称/密钥有原生校验，错误不回显密钥。Unix 下本产品数据目录为 0700、数据库为 0600；本地凭证沿用数据库存储方式，并未声称使用系统钥匙串或应用层加密。

## 功能边界

当前 HQ 页面管理连接配置与默认选择，**保存成功不代表模型 API 连通，设为默认不代表已有请求转发**。本次没有新增自动发现、HQHarness 配置注入、`/hq/v1` 推理服务或真实模型调用；这些仍属于 [HQHarness 接入设计](hq-branding-plan.md) 的后续范围。界面因此没有“已连接”“路由生效”或虚假的连通测试状态。

原数据库的完整表结构和历史代码继续保留，便于后续复用和升级；隐藏的是产品运行入口，不是通过删表或替换第三方品牌实现。原版 App 的测试仍可运行，但它们不能代替 HQ 入口的测试。

## 本次验证

- TypeScript 类型检查、Prettier 检查通过。
- HQ 界面测试 4 项通过：旧导航不会复活隐藏入口、编辑保留密钥、默认选择/删除确认、失败保留草稿。
- HQ 原生存储测试 2 项通过：隔离与脱敏、编辑/默认/删除、revision 冲突、无效地址及事务保持。
- 浏览器实际渲染检查：新增连接、保存、默认选择、编辑、设置、明暗主题通过；控制台无错误。原生边界使用虚拟数据，没有调用真实供应商。命令记录只有 HQ 存储、应用版本、主题和启动设置，没有旧 provider/proxy/mode/auth/session 请求。
- macOS 未签名 debug 应用构建成功；关闭开发服务器后用全新 `CC_SWITCH_TEST_HOME=/private/tmp/hq-only-smoke-20261022` 启动。日志确认进入 HQ-only 分支；只读检查无旧客户端供应商桶、未创建旧客户端目录，数据库/目录权限为 0600/0700。
- Windows/Linux 的代码路径及构建配置保留，本轮仅在 macOS 编译启动；未宣称三平台原生验收通过。

本地证据（不随源码提交）：`validation/hq-only-ui-audit.json`、`hq-native-startup-audit.json`、`hq-ui-tests.log`、`hq-native-tests.log`、`hq-native-build.log`、`hq-native-smoke.log`。该轮使用本机 Node 26.4.0 和仓库 Rust 1.95；沿用前一阶段锁文件，没有升级 UI 框架。

本地截图（不随源码提交）：`validation/hq-only-main-light.png`、`hq-only-main-dark.png`、`hq-only-editor.png`、`hq-only-settings.png`。它们是 HQ 界面与虚拟 API 数据的渲染预览。

验证包已更新至 `release/macos/HQ Model Switch.app`，仍为未签名本机验证包。测试应用退出、测试开发服务器停止。本机缓存明细保留在本地验证资料中。
