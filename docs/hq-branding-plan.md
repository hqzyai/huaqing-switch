# HQ Model Switch 品牌化与 HQHarness 接入设计

> 历史设计研究：本文保留最初核查结果和当时的待办，并非当前实施状态。产品现名为 **Huaqing Switch**，已实现本地推理服务与 HQHarness 接入；当前契约与验收限制见 [接入说明](huaqing-harness-integration.md)。

状态：**品牌、共享 UI 和 HQ-only 工作区已实施；HQHarness 运行时接入仍待实施。** 连接配置采用独立 HQ 存储，路由/聚合和其他客户端入口已隐藏，旧客户端启动副作用已门控，见 [hq-only-workspace.md](hq-only-workspace.md)。品牌记录见 [branding-ui-migration.md](branding-ui-migration.md)。本文保留独立推理 API、自动发现与数据面设计；这些能力尚未实现，不能把 UI/配置存储验收当作模型接入验收。

日期：2026-10-22。

- 产品名称：**HQ Model Switch**。
- 项目目录：`Huaqing Model Connection`。目录名称不等同于修改产品名称。
- 上游：<https://github.com/farion1231/cc-switch>。
- 核查基线：`cb90a2440d1a771d67f46b65b2e0f2ea5106ce85`，官方源码归档；文件和行号均指该基线。
- 用户选择：基于 CC Switch 品牌化，HQHarness 优先，其他客户端入口默认隐藏。之前评估的自建 Go 网关不再是当前方案。

## 目标行为

打开 HQ Model Switch 后，默认进入 HQHarness 的供应商与模型管理页，启动属于本产品的本地代理。HQHarness 首次连接固定的本地地址和访问凭证，此后通过稳定的供应商/模型标识调用上游；进行中的工具调用循环不会因其他页面切换而换到另一供应商。

其他客户端适配代码保留，入口默认隐藏，用户明确启用后才参与相关配置导入或管理。默认运行不读取、接管或改写 Claude Code、Codex 等旧客户端配置。品牌、存储、协议注册、端口和更新来源与上游 CC Switch 隔离，使两个产品能够并存。

目标中的品牌、存储隔离、HQ 专用默认页、其他客户端隐藏及后台副作用门控已实施。HQ 连接记录可维护，但面向 HQHarness 的稳定推理路由和访问凭证契约仍待实现和验收。

## 1. HQ 客户端类型与界面

新增独立的 `AppType::Hq` / `AppId = "hq"`，不把 Codex 重命名为 HQ，也不让 HQ 请求写入 Codex 的供应商桶。

需要同步的最小位置：

| 位置                                                                          | 已核实的基线行为                                                            | 待实现改动                                                                          |
| ----------------------------------------------------------------------------- | --------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `src-tauri/src/app_config.rs:410`                                             | `AppType` 为封闭枚举，配有 `as_str`、`FromStr`、`all` 和能力判断            | 添加 HQ，逐项定义其代理、路由与聚合能力，不继承不需要的 MCP/Skills/旧客户端文件投影 |
| `src/lib/api/types.ts:2`、`src/types.ts:307`                                  | 前端有独立 AppId 联合类型与 VisibleApps 接口                                | 添加 HQ 类型及可见性                                                                |
| `src/config/appConfig.tsx:19`                                                 | `APP_IDS`、`DEFAULT_VISIBLE_APPS`、代理/路由/聚合能力列表和图标映射集中维护 | HQ 排第一且默认可见，旧客户端默认隐藏；只为真正实现的能力打开开关                   |
| `src/App.tsx:145`、`:217`                                                     | 初始客户端及空列表回退仍是 Claude                                           | 默认与回退改为 HQ，处理旧 localStorage 无效值                                       |
| `src/components/shell/AppGlyph.tsx:7`、`src/components/shell/Sidebar.tsx:231` | 显示名映射与侧栏可见列表                                                    | 添加 HQ 名称/图标，复用可见性过滤                                                   |
| `src-tauri/src/settings.rs:30`                                                | Rust VisibleApps 有自己的字段默认值和 Default 实现                          | 同步为 HQ 默认开启、旧客户端默认关闭，避免前后端默认值不一致                        |
| `src-tauri/src/tray.rs:1976`                                                  | 托盘按 VisibleApps 过滤，但另有固定 TRAY_APPS 范围                          | 定义 HQ 托盘行为；隐藏客户端不出现，也不成为默认回退                                |

仅改 UI 默认值不够：`src-tauri/src/lib.rs:805` 的启动流程仍遍历 `AppType::all()`，自动导入旧客户端 live 配置及 seed。应为“启用客户端”定义一份后端策略并用于启动导入/seed、启动恢复及定时探测；UI 可见性只是表现层，不应被误认为后端副作用开关。首次启动只处理 HQ；启用旧客户端时再明确触发对应导入。需检查 `lib.rs` 内其余 `AppType::all()` 调用，防止遗漏。

## 2. HQ 独立代理命名空间

建议 HQ 使用 `/hq/v1` 命名空间；入口按实际完成的协议逐个开放，未实现的协议不能只挂一个别名冒充支持。

| 位置                                                          | 核查发现与实现方向                                                                                                                      |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `src-tauri/src/proxy/server.rs`                               | 路由集中挂载；HQ 可新增 `/hq/v1/models` 与已验收的推理端点                                                                              |
| `src-tauri/src/proxy/handlers.rs:933`                         | Chat Completions handler 硬编码 Codex 上下文；需抽出按应用参数化的共有逻辑，再由 HQ wrapper 传入 HQ 类型，不能仅复制 URL 到现有 handler |
| `src-tauri/src/proxy/handlers.rs:310`、`:1035`                | Messages 与 Responses 已有按应用参数化的内部函数，可评估复用；必须检查其中各协议与应用专属分支                                          |
| `src-tauri/src/proxy/handler_context.rs::RequestContext::new` | 已接受应用类型、日志标签、app_type 字符串以及可选 StackTarget；适合复用请求生命周期逻辑                                                 |
| `src-tauri/src/proxy/provider_router.rs`                      | 熔断器 key 已按 `app_type:provider_id` 隔离，数据库供应商查询也按应用过滤                                                               |
| `src-tauri/src/proxy/providers/mod.rs:139`、`:267`            | ProviderType 推断及 adapter 选择仍需添加 HQ 分支，不能把客户端身份与上游协议混为同一概念                                                |
| `src-tauri/src/mode/stack.rs:54`、`:118`、`:163`              | 聚合支持范围及模型编码/解码按应用写死；HQ 需定义自己的稳定模型标识与解析规则                                                            |

`/hq/v1/models` 应输出 HQHarness 真正消费的模型目录。现有通用路径 `handle_models` 包含 Codex 专属 `{models: ...}` 目录及 Claude discovery 分支，不能假设能直接当作标准 OpenAI `{object:"list", data:[...]}` 响应使用。

HQ 请求应通过稳定的 `routeId/modelId` 选择具体供应商与上游模型；可复用 `StackTarget` 的“直达指定供应商、关闭故障转移、不读写全局路由状态”思路。不采用会被全局切换改指上游的 `hq-default` 别名。修改/删除正在使用的路由须保证旧任务一致性，或显式拒绝影响活跃任务的变更。路由策略、并发行为和访问凭证均尚待实现。

## 3. 数据库前向迁移

添加枚举后直接向现有代理表写入 `app_type = 'hq'` 会失败：

- `src-tauri/src/database/schema.rs:132` 的 `proxy_config` 定义含固定 CHECK：`claude/codex/gemini/grokbuild`。
- 历史重建表定义在同文件 `:926`、`:1488` 也含该约束。
- `src-tauri/src/database/dao/proxy.rs:244` 的按应用补行及全局配置镜像逻辑需要同步检查。

实现必须增加明确的前向 schema 迁移，重建受 CHECK 限制的表并保留现有数据，添加 HQ 初始行；新建数据库路径也要包含 HQ。不要只改旧建表 SQL，已有数据库不会因此自动更新。旧版本兼容入口、代理全局配置读取和启用状态的假设也须核查。`providers` 以 `(id, app_type)` 为主键，现有结构可承载独立 HQ 供应商桶。

迁移需验证空库、已有本产品数据库、重复启动幂等及失败回滚；不能借用上游真实用户数据库做首次运行验证。这些验证尚未执行。

## 4. 品牌、存储、协议及更新隔离

| 范围              | 必须检查的位置                                                                                                                  | 待实现要求                                                                                                                  |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| 系统身份          | `src-tauri/tauri.conf.json`、`src-tauri/tauri.windows.conf.json`、`src-tauri/Cargo.toml`、`package.json`                        | 产品名、bundle identifier、包名和可执行文件身份独立；最终标识值由实施统一确定                                               |
| 本地数据          | `src-tauri/src/config.rs:314`、`src-tauri/src/settings.rs:702`、`src-tauri/src/database/mod.rs:101`、`src-tauri/src/lib.rs:595` | 独立配置目录、DB、设置、live-state、备份及日志；settings_path 直接拼 `.cc-switch`，只改统一配置目录函数会漏掉它             |
| 自定义路径 Store  | `src-tauri/src/app_store.rs`                                                                                                    | `app_paths.json` 的 Store 依赖应用身份；不读取上游的目录覆盖值，不回退到上游 `.cc-switch`                                   |
| 前端存储          | `src/lib/navigation.ts:64`、`src/contexts/UpdateContext.tsx:32`、`src/index.html:7`                                             | 统一产品自己的 localStorage key，包括上次客户端、视图、主题和更新提示状态                                                   |
| 深度链接          | `tauri.conf.json`、`src-tauri/src/lib.rs:274`、`src-tauri/src/deeplink/parser.rs:22`                                            | 更换 scheme 并同步解析/注册，避免抢占 `ccswitch://`；未支持时关闭入口                                                       |
| 本地端口          | `src-tauri/src/proxy/types.rs`、数据库默认值及接入配置                                                                          | 选择与上游默认 15721 不冲突的产品端口；占用时明确报错，不能悄悄连接别的产品                                                 |
| 自动更新          | `tauri.conf.json`、`src/lib/updater.ts`、`src/contexts/UpdateContext.tsx`、`src-tauri/src/lib.rs:581`                           | 上游公钥与 `dl.ccswitch.io` / 上游 GitHub release endpoint 必须移除或禁用；只有本产品自己的发布、签名、校验齐备后才启用更新 |
| Windows 安装/升级 | `src-tauri/wix/per-user-main.wxs`                                                                                               | 检查模板中的 `cc-switch.exe` 关闭目标、更新任务、固定 Component GUID 和生成的 UpgradeCode，避免关闭、升级或卸载上游程序     |
| Linux 分发        | `flatpak/com.ccswitch.desktop.yml` 及关联 desktop/metainfo                                                                      | 使用本产品自己的应用 ID、执行名、文件名和权限说明                                                                           |
| UI 与发布         | Sidebar/About、`src-tauri/src/tray.rs`、图标、各语言文案、`.github/workflows/release.yml`                                       | 更换产品标识与下载入口；保留上游来源和许可证致谢，不冒用上游“唯一官网”或更新承诺                                            |

上游 MIT 允许修改和分发，需保留其版权及许可声明；源码内的许可证和第三方声明不能作为普通品牌字符串批量替换。参见本项目 `LICENSE` 与[上游基线 LICENSE](https://github.com/farion1231/cc-switch/blob/cb90a2440d1a771d67f46b65b2e0f2ea5106ce85/LICENSE)。

## 5. 本次核查的交付与边界

初次调研已完成源码核查和独立目录导入；后续品牌实施已完成身份/数据/更新隔离与共享 UI 改造，验证结果另见迁移台账。接下来需要添加 HQ 类型、数据库迁移、独立 API 和 HQHarness 连接，再按真实目标供应商验证流式、工具调用、取消、并发、重启恢复及与上游并存。本文第 4 节是原始实施检查清单，最终实际参数以 `brand.config.json` 及迁移台账为准。

主要官方源码引用：

- [应用类型](https://github.com/farion1231/cc-switch/blob/cb90a2440d1a771d67f46b65b2e0f2ea5106ce85/src-tauri/src/app_config.rs)
- [前端客户端配置](https://github.com/farion1231/cc-switch/blob/cb90a2440d1a771d67f46b65b2e0f2ea5106ce85/src/config/appConfig.tsx)
- [启动与自动导入](https://github.com/farion1231/cc-switch/blob/cb90a2440d1a771d67f46b65b2e0f2ea5106ce85/src-tauri/src/lib.rs)
- [代理请求处理器](https://github.com/farion1231/cc-switch/blob/cb90a2440d1a771d67f46b65b2e0f2ea5106ce85/src-tauri/src/proxy/handlers.rs)
- [数据库 schema](https://github.com/farion1231/cc-switch/blob/cb90a2440d1a771d67f46b65b2e0f2ea5106ce85/src-tauri/src/database/schema.rs)
- [应用身份、深链与更新配置](https://github.com/farion1231/cc-switch/blob/cb90a2440d1a771d67f46b65b2e0f2ea5106ce85/src-tauri/tauri.conf.json)
