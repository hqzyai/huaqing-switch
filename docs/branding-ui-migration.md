# HQ Model Switch 品牌与 UI 迁移记录

日期：2026-10-22。目录：`Huaqing Model Connection`。本轮交付是已落地、可构建和审查的品牌/UI 改造；尚不是完成 HQHarness 接入、三平台原生验收及签名发布的成品。

**后续状态更新：** 用户随后要求仅显示 HQ Harness。现已启用独立 HQ-only 界面、连接配置存储和原生启动分支，隐藏路由/聚合及其他入口；详见 [hq-only-workspace.md](hq-only-workspace.md)。本文保留品牌阶段的原始验证范围与数据，不代表当前仍显示多客户端界面。

## 产品身份与安装隔离

| 项目              | 本产品实际值                                                                           |
| ----------------- | -------------------------------------------------------------------------------------- |
| 产品 / 包 / 版本  | HQ Model Switch / `hq-model-switch` / `0.1.0`                                          |
| 应用 ID           | `app.hqmodelswitch.desktop`                                                            |
| 默认数据 / 数据库 | `~/.hq-model-switch/` / `hq-model-switch.db`                                           |
| 日志文件          | `hq-model-switch.log`                                                                  |
| 深链              | `hqmodelswitch://`；Tauri 配置、macOS 合并 plist、解析器及 Linux 注册一致              |
| 代理默认端口      | `17621`                                                                                |
| 前端存储          | 主题、导航、缓存、会话、用量、托盘等改用本产品前缀                                     |
| 同步默认目录      | `hq-model-switch-sync`                                                                 |
| 更新              | 默认关闭；前端不调用更新插件，原生更新命令拒绝未配置的更新；无上游公钥、镜像或下载回退 |

`brand.config.json` 是品牌参数清单，前端通过 `src/lib/brand.ts` 读取，原生更新策略编译时读取。原生必须使用字面量的路径/配置仍保留在对应模块；资产准备脚本校验包名、版本、应用 ID、深链与 CSP，不能把修改清单文件视为自动改完全部路径。

Windows WiX 的进程名和固定任务组件 GUID 已隔离；Linux Flatpak/desktop/metainfo 已改用本产品身份。原版发布、镜像与 Claude 自动化工作流移至 `docs/upstream-workflows/*.reference`，不会作为活动工作流运行。CI 保留三平台检查矩阵并先准备品牌资产，但本轮没有推送或触发远端 CI。

MIT LICENSE 保留原文和上游版权。旧 Codex provider ID、历史迁移桶、模型目录文件名、加密摘要封装前缀等是兼容协议，保留其原值；第三方供应商标志、文档 URL、上游历史版本说明也不作为本产品品牌字符串替换。关于页明确区分 HQ 产品与 CC Switch 来源。

## 采用的规范与资产

规范来源为用户提供的 `shadcn-ui-unify/`，主题 `shadcn-neutral-v3`，图标 `lucide-outline-v1`，标志 `hqzy-logomark-v2`。沿用 React 18 / Tailwind 3 / 现有 Radix 组件；没有执行 shadcn init 或浮动升级到另一套组件实现。

| 资产               | 实际交付与许可                                                                                            |
| ------------------ | --------------------------------------------------------------------------------------------------------- |
| HQZY 图形          | 使用规范中的原始蓝色 SVG，viewBox `793×568`，保留透明背景、渐变与比例；用户提供品牌资产，不声称开放再许可 |
| SVG SHA-256        | `720d90e26fcd94a1000c5a40cd2dd042c07299f5bae601ba7e3052330e534c52`                                        |
| Geist / Geist Mono | Fontsource Variable `5.2.6`，本地 woff2，OFL                                                              |
| Noto Sans SC       | Fontsource Variable `5.3.1`，完整 normal/weight Unicode 子集，本地 woff2，OFL                             |
| 操作图标           | 保留锁文件中的 lucide-react `0.542.0` 及其 LICENSE，轮廓描边 `1.75`、非缩放描边；不覆盖品牌图形           |

`scripts/prepare-brand-assets.mjs` 在安装、开发和构建时验证标志哈希、复制 107 个字体子集，生成精确字体家族别名、favicon、桌面图标及许可证清单。字体家族为 Geist / Noto Sans SC / Geist Mono，关闭字体合成。生成的字体、图标和许可证目录不入 Git；完整规范目录与锁文件是可重建输入，不能只分发 SKILL.md。

运行包包含字体文件和 `licenses/`；其中 `assets.json` 记录版本、权利说明及每个字体文件的 SHA-256。应用包另附上游 MIT、UPSTREAM.md 和品牌参数。图标转换使用项目已有的 Tauri CLI，将非正方形原图等比置于不透明的纯白方形背景上。

参考官方接口：[shadcn theming](https://ui.shadcn.com/docs/theming)、[shadcn Button](https://ui.shadcn.com/docs/components/button)、[Lucide React](https://lucide.dev/guide/packages/lucide-react)。它们用于核对语义和接口；本次保留了仓库已有定制行为，没有声称所有组件均被最新官方源码替换。

## Token 与组件落点

| 规范输入        | 项目承载                                                                           | 主要消费者                                                      |
| --------------- | ---------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| 明暗语义颜色    | `src/index.css` 中的 HSL token；既有 `--bg-*` / `--text-*` / `--action-*` 别名映射 | 页面、侧栏、表单、共享控件及 portal                             |
| 文字角色 / 字体 | `tailwind.config.cjs` 的 body/strong/caption/title 与字体族；生成的 fonts.css      | 产品界面、设置、供应商卡片；代码区域使用 mono                   |
| 控件与圆角      | control 6px，panel 12px，菜单/浮层 8px，badge 4px，tooltip 6px                     | Button、Input、Select、Card、Dialog、Badge、Popover、Tooltip 等 |
| 控件密度        | 标准 36px、compact 32px；焦点、hover、active、disabled 状态集中处理                | 表单、设置、搜索、供应商管理                                    |
| 阴影 / 覆盖层   | 规范中的明暗阴影和遮罩；卡片去装饰阴影                                             | Radix portal、对话框、菜单                                      |
| Logo            | 同源 SVG，标准 28px 图像框等比显示                                                 | 侧栏、关于页、favicon；安装图标为纯白背景转换版                 |

原橙色操作主题改为规范中的中性色。直连/路由/聚合、健康状态、图表和供应商品牌颜色保留业务含义。常规设置和供应商界面的局部字级、圆角与操作图标一并调整；现有导航、表单状态、路由、请求协议和拖拽逻辑不因换肤重写。

## 行为与几何兼容保留

| 位置                                            | 保留内容与原因                                                                                  | 范围 / 验证状态                                                       |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| SessionReader、用量图表、虚拟化列表、CodeMirror | 原测量行高、时间轴/代码布局和业务色；强改几何会影响虚拟滚动和双向内容                           | 共享颜色/字体仍继承；已有测试运行，未完成所有长内容及虚拟滚动人工验收 |
| FullScreenPanel                                 | 保留导航式覆盖层，未冒充 Radix modal；输入框有焦点时原有 Escape 策略不变                        | 供应商选择页已检查；移开输入焦点后 Escape 可返回                      |
| Dialog / Select / Popover / Dropdown            | 保留现有 Radix 实现、嵌套 portal、滚动与焦点处理                                                | 共享视觉已迁移；未人工穷举所有嵌套组合和焦点返回                      |
| Checkbox                                        | 保留现有原生三态实现及 change/ref 契约                                                          | 没有仅为样式替换组件实现                                              |
| Switch sm                                       | 密集表格保留 26×16；标准档 32×18                                                                | 相关组件测试通过；不宣称小档满足所有触屏目标尺寸                      |
| 侧栏                                            | 保留原宽度、拖拽区、macOS 红绿灯几何和折叠逻辑；品牌名称沿用截断机制，有完整 title 及底部产品名 | Windows/Linux 紧凑图像框局部 22px，其余采用 28px；原生仅验证 macOS    |
| 旧版本说明                                      | 原版历史内容保留；无 HQ 发行仓库时禁用旧版弹窗下载按钮；关于页改为来源与许可                    | 禁用下载有针对性测试；不把上游更新日志当作 HQ 发布服务                |

## 实际验证

本机为 Apple Silicon macOS；Rust 使用仓库固定的 1.95。前端测试和构建实际使用本机 Node 26.4.0，仓库及 CI 固定 Node 22.12.0；本轮没有在 Node 22 或 Windows/Linux 上运行，版本差异不能隐去。

| 检查                        | 实际结果                                                                                                                          |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| frozen lockfile 安装        | 通过                                                                                                                              |
| TypeScript `tsc --noEmit`   | 通过                                                                                                                              |
| 源码 Prettier / Rust fmt    | 通过                                                                                                                              |
| Vite 生产构建               | 通过；存在原有大于 500kB chunk 提示，未进行无关的拆包重构                                                                         |
| `cargo check --locked`      | 通过                                                                                                                              |
| `cargo test --lib --locked` | **3930 通过，0 失败，11 ignored**；最后一次全量为 localhost 设置 NO_PROXY，避免本机代理把拒绝连接测试变成 HTTP 502                |
| 前端完整测试                | **3159 通过，3 失败**；上游基线 **3158 通过，同样 3 失败**，新增更新禁用测试通过                                                  |
| 定向前端回归                | PiProviderForm、IconPicker、NewLayoutDialog、更新禁用共 64 项通过；OAuth/Switch 共 23 项通过                                      |
| 更新策略补充回归            | 数据库恢复不触发更新检查、无上游发布按钮，以及禁用旧下载入口的定向检查通过；见 `update-policy-tests.log`                          |
| 兼容协议补充回归            | 恢复原有 Codex 历史迁移样本中的协议表名后，Codex 相关测试 **1029 通过，0 失败，1 ignored**；见 `native-codex-regression.log`      |
| macOS 原生打包              | `--debug --bundles app --no-sign --ci` 成功，最终合并 plist 的产品名、ID、协议均核对                                              |
| macOS 真实启动              | 隔离 `CC_SWITCH_TEST_HOME` 启动；关闭开发服务器后读取到 `tauri://localhost` 内置页面、HQ 窗口标题和侧栏；未调用真实模型 API       |
| 浏览器实际渲染              | 原应用 UI + 虚拟原生数据；主界面、设置/关于明暗主题、供应商选择、Escape 返回已检查；无控制台错误                                  |
| 实际渲染字体                | CDP 平台字体报告确认 Geist-SemiBold、Geist-Regular、Noto Sans SC 均为 custom font；Noto 可变字体内部名含 Thin，不等于发生字体回退 |
| 视口检查                    | 1000 / 768 / 390 CSS px 的主界面文档宽度无新增横向溢出；桌面应用原最小窗口尺寸仍保留                                              |

三项基线失败为：`UsageDashboard` 的请求日志列 class 断言、tab/tabpanel aria 关联断言，以及 `rememberedChoice` 的 storage unavailable 断言。本次没有把它们改为跳过，也没有宣称前端测试全绿。Node 26 同时存在实验性 localStorage 提示；未断言该版本是全部失败的根因。

原生窗口检查按用户要求先用 arc_driver；apps/windows 成功，但 observe/screenshot 超时，随后使用 cua 读取真实窗口。该后备读取也出现长时间延迟，已停止进一步原生界面遍历、退出本次测试进程并 release。没有修改现有 CC Switch 安装、真实账户凭证或 HQHarness 源码。

证据保留在本地 `validation/`（不随源码提交）：

- `ui-main-light.png`、`ui-main-1000.png`、`ui-provider-picker.png`：当前界面（虚拟原生数据）。
- `ui-about-light.png`、`ui-about-dark.png`：关于页明暗主题。
- `ui-upstream-baseline.png`：从独立基线源码重建的上游界面。
- `ui-audit.json`：视口、字体、控制台、Escape 返回结果。
- `bundle-identity.txt`、`macos-binary.sha256`：最终应用元数据和可执行文件摘要。
- `baseline-tests.log`、`tests-acceptance.log`、`native-tests-direct.log`、`native-build-final.log`、`native-smoke.log`：本机日志，忽略入 Git。

尚未验收：全部共享组件的交互矩阵、200% 缩放、320px、文本间距重排、高对比/辅助技术、所有 RTL/语言组合、真实供应商流式与工具调用、Windows/Linux 原生安装运行。不能据本轮截图宣称全量行为等价、WCAG 合规或三平台生产发行就绪。

## 交付文件与后续接入

本机包：`release/macos/HQ Model Switch.app`，是本机验证用的未签名 debug 应用，不是正式签名/公证发行包。正式发行还需产品自己的签名、构建/分发及更新服务；没有借用原版发布身份。

HQHarness 专用 `AppType::Hq`、数据库前向迁移、独立 `/hq/v1` 接口、稳定模型路由、访问凭证和其他客户端默认隐藏/后台门控尚待实施。当前仍显示和运行原有客户端功能，不以改名 Codex 的方式冒充 HQ 接入。设计见 [hq-branding-plan.md](hq-branding-plan.md)。

构建缓存、磁盘占用及清理明细保留在本地验证资料中。验证副本仅还原源码并复用依赖，未复制已有 target/node_modules；日志和交付包位于构建缓存之外。

本轮改动保留为工作区 diff，未自动提交、推送或发布。
