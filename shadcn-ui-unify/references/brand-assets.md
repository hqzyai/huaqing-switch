# favicon 与控制台品牌资源

完整迁移或用户要求统一品牌入口时读取。`hqzy-logomark-v2` 是用户于 2026-10-13 指定的品牌预设：**浏览器 favicon 和控制台 Logo 都使用同一份蓝色小标志，不使用文字字标。** HQZY 品牌项目或用户明确指定此预设时采用；其他品牌沿用其已指定资产。保留项目名称和页面标题。仅修改技能时，不自动修改项目。

旧提示词中的 `hqzy-agentos-v1` 是当前预设的兼容别名，统一解析为 `hqzy-logomark-v2`；不恢复已移除的文字字标。

## 唯一内置资源

[hqzyai-logomark.svg](../assets/branding/hqzyai-logomark.svg) 是完整 SVG 文件，图形和渐变均内置，无外链图片、字体或远程运行依赖。`branding.favicon.asset` 和 `branding.consoleLogo.asset` 必须指向这同一文件。

| 用途 | 目标项目静态文件 | 展示 |
| --- | --- | --- |
| 浏览器标签页 favicon | `hqzyai-logomark.svg` | SVG favicon，保留非正方形比例 |
| 控制台侧栏/顶栏品牌入口 | 复用同一文件 | 标准 28×28px、紧凑 24×24px 图像框，`object-fit: contain` |

viewBox 为 `0 0 793 568`。资源哈希及展示档位以 [neutral-theme.json](../assets/neutral-theme.json) 的 `branding` 为准。内置副本是用户提供的品牌资产，未提供开放再许可声明；不能声称属于 Lucide ISC、shadcn MIT 或字体 OFL。

## 跨电脑分发与资源定位

- 分发或安装时复制完整 `shadcn-ui-unify/` 目录，包含 `SKILL.md`、`agents/`、`references/`、`assets/neutral-theme.json` 和 `assets/branding/hqzyai-logomark.svg`。只发送提示词或 `SKILL.md` 不会携带图标。
- 所有技能内资源路径相对于当前 `SKILL.md` 所在目录解析，不能相对于目标项目的工作目录猜测，也不能写死作者的用户名、家目录或源项目路径。不把 SVG 换成符号链接或需要远程下载的 URL。
- 执行迁移时先检查内置 SVG 存在并与 JSON 中的 SHA-256 一致，再复制到目标 Web 应用静态目录；目标已有同哈希文件则复用。应用运行时只读取项目里的副本，因此部署后不需要安装技能。
- 打包交付时检查压缩包包含全部相对引用文件，并在另一临时目录解压，从解压目录读取 JSON、定位 SVG、校验哈希并复制到临时项目；校验过程无需原始技能目录、源项目或网络。SVG 外部引用应为空，本地渐变 `url(#...)` 可保留。
- 若内置文件缺失或损坏，明确报告缺少的资源并要求补齐完整包；不静默换成文字、Lucide 图标或旧字标。其他可独立验证的 UI 工作可以继续。

## 接入规则

### 标签页 favicon

- 把内置 SVG 复制到目标 Web 应用的静态目录，按项目的静态 URL/部署前缀引用。检查应用入口、嵌套路由和子路径部署，不把磁盘路径写进网页。
- Next.js App Router 使用当前版本支持的 metadata/文件约定。配置 `metadata.icons.icon` 时使用 SVG MIME，并复用项目既有 base-path helper；不默认新增环境变量，不覆盖 title、description、主题初始化或其他 metadata。
- 检查已有 `favicon.ico`、`icon.*` 与 `<link rel="icon">`，只处理同一 favicon 入口的冲突。apple-touch、PWA manifest、桌面安装包图标不在此预设的两个入口内。
- 非 Next 项目沿用 HTML/head 管理方式和 bundler 的 base URL 处理。必须提供 ICO/PNG 回退时，沿用项目转换工具等比生成并保留透明留白。
- 原图不是正方形，不能拉伸或裁剪以填满标签页图标槽。小尺寸可读性不足时报告实际效果，不静默重画。

### 控制台蓝色小标志

- 在现有共享品牌组件中使用同一个 `hqzyai-logomark.svg`，不显示旧文字字标，不添加新的品牌文字。沿用目标项目的静态 URL helper。
- 图像框标准 28×28px、紧凑 24×24px；`object-fit: contain`、`flex-shrink: 0`、`display: block`。方形框内的 SVG 仍按 793:568 等比显示，不拉伸填满。原有控件更紧凑时只在局部等比适配并登记尺寸。
- 保持原始蓝色、渐变、透明背景和比例，不加滤镜、`currentColor`、圆角遮罩或新 hover 动画。保留原命中区、入口位置和导航布局。
- 单独品牌图片默认 `alt="HQZY"`、`draggable={false}`；父链接已有完整可访问名称时使用空 alt，避免重复播报。保留父链接、href、点击/键盘行为、侧栏切换、title 和 tooltip，不让图片新增 Tab 停靠点。
- 明暗主题使用同一原始资源；可见性不足时记录缺少的变体，不自行反色。品牌蓝色不自动成为全站按钮或背景颜色。
- 迁移旧版预设时更新资源引用及字标专用宽度。确认旧字标没有其他消费者后移除无用副本；不批量改动第三方商标和用户内容。

## 验收

1. 记录 `hqzy-logomark-v2`、资源哈希、favicon/head 入口、共享品牌组件及部署前缀；同一图标只复制一份。
2. 检查最终 head、URL、HTTP 200 和 `image/svg+xml`，验证浏览器 favicon。遇到缓存先刷新或使用独立标签，不清除用户站点数据。
3. 检查控制台标准/紧凑、窄屏、原有主题及 200% 缩放；确认图标无变形、裁切或新增溢出，导航及可访问名称保持原意。保存含入口周边的截图。
4. 运行受影响应用已有类型检查/构建。交付时区分实际验证、局部尺寸例外和未验证项。分发技能包时同时报告上述异地解压校验结果。
