# Lucide 图标规范

完整迁移或统一图标时读取。通用 UI 图标的唯一目标库是 [Lucide](https://lucide.dev/)，React 使用官方 `lucide-react`。本技能的图标子规范为 `lucide-outline-v1`；尺寸和描边是本技能的设计决定，不是 Lucide 或 shadcn 的默认要求。数值来源为 [neutral-theme.json](../assets/neutral-theme.json) 的 `icons`。

## 来源与版本

2026-10-12 核对过以下官方资料；实际实施仍以锁定版本的源码、类型和对应官方文档为准：

- [React 接入](https://lucide.dev/guide/react/getting-started)：官方包、具名导入和 SVG props。
- [描边](https://lucide.dev/guide/react/basics/stroke-width)：尺寸缩放与固定描边的区别。
- [无障碍](https://lucide.dev/guide/react/advanced/accessibility)：装饰图标和可访问名称。
- [动态图标](https://lucide.dev/guide/react/advanced/dynamic-icon-component)：按名称加载的适用范围及构建成本。
- [许可证](https://lucide.dev/license)：ISC；其中列出的 Feather 衍生图标还带有 MIT 声明。保留所用发布物完整版权和许可，不笼统标注成全库 MIT。
- [品牌 Logo 声明](https://lucide.dev/brand-logo-statement)：品牌资产不作为通用图标替换。

已有 `lucide-react` 时复用符合需求的锁定版本，不为换图标自动升级大版本。没有时，在实际消费它的 app/shared package 中按项目包管理器安装兼容版本并更新锁文件；monorepo 不默认把依赖塞进根包。核对 React peer dependencies、实际导出名与 ref API；不要因为官网出现新 prop 就假定本地版本支持。

## 固定视觉规则

| 角色 | 尺寸（根字号 16px 时） | 使用位置 |
| --- | --- | --- |
| small | 1rem / 16px | 按钮内、输入前后缀、菜单、表格行操作、紧凑工具栏 |
| default | 1.25rem / 20px | 一级导航、侧栏、工具栏独立入口 |
| large | 1.5rem / 24px | 已有空状态或提示区域的独立图标 |

- 单色线性图标；`currentColor`，`fill="none"`，圆端点、圆连接。保留官方路径，不手改 path、拉伸 viewBox 或用 CSS border 拼装相似图标。
- 描边目标 **1.75 CSS px，在三个档位保持一致**。当前官网使用 `nonScalingStroke`，一些已安装版本使用 `absoluteStrokeWidth`；只采用目标版本实际支持且验证过的方式。后者依赖 `size` 计算，不能给 `size={24}` 后再用 CSS 偷改为 16px；检查真实渲染、缩放和样式覆盖，不只看 JSX 数值。无法保持固定描边时记录局部例外，不向 DOM 传入未知 prop。
- 图标宽高相等，保持比例、居中对齐、禁止 flex 挤压；与文本间距统一 8px。普通 inline 图标沿用原行高/基线；不因图标替换增加容器、改变命中区域或推挤文字。
- 原有尺寸承担虚拟化、拖拽手柄或特殊布局契约时，保留最小局部几何并登记原因，其他可独立样式仍统一。图标 16px 不等于按钮 16px；控件命中区按 density/sizes 执行。
- 颜色从所属控件的前景 token 继承；次要图标用 muted-foreground，已有语义提示才用 success/warning/info/destructive。禁用态随父控件处理，避免内外两次 opacity。深色模式也走语义 token。
- hover、active、focus 不改描边粗细、不缩放；selected 沿用原背景/indicator，不统一改成实心。原有实心/空心区别若承担选中含义，使用经核对的 Lucide 状态配对或保留局部表现并登记，不丢失状态信息。
- 保留已有加载、旋转、展开时机和方向；不增加 hover 动画或动画图标库。已有无限旋转在 reduced-motion 下提供静态可辨识的忙碌反馈，原状态文案/播报继续工作。

## 语义映射

同一动作跨页面使用同一个图标，在现有共享图标层集中维护映射。以下为常用选择，实施前核对所装包确实导出；不是要求给原来没有图标的地方新增图标。

| 含义 | Lucide 选择 |
| --- | --- |
| 搜索 / 设置 | `Search` / `Settings` |
| 新增 / 关闭 | `Plus` / `X` |
| 编辑 / 删除 | `Pencil` / `Trash2` |
| 复制 / 完成 | `Copy` / `Check` |
| 上传 / 下载 | `Upload` / `Download` |
| 展开方向 | `ChevronDown` / `ChevronRight`（按原状态和方向映射） |
| 更多菜单 | `Ellipsis` / `EllipsisVertical`（保留原方向） |
| 信息 / 警告 / 错误 / 成功 | `Info` / `TriangleAlert` / `CircleAlert` / `CircleCheck` |
| 加载 / 刷新 | `LoaderCircle` / `RefreshCw`（不同动作不混用） |
| 播放 / 暂停 / 停止 | `Play` / `Pause` / `Square` |

其余动作在 [官方图标目录](https://lucide.dev/icons/) 选择语义匹配的图标，并加入同一映射。不要用“看起来近似”的图标模糊上传/导出、关闭/删除、刷新/同步、当前状态/可执行动作；不改文案、翻译 key、触发回调或状态条件来适配图标。已有 RTL 只按原方向策略镜像有方向语义的图标，不能把所有 SVG 全部翻转。

## 接入与迁移

1. 盘点包依赖、共享 Icon 封装、具名/动态导入、自绘 SVG、图片图标、icon font、CSS mask/background/pseudo-element；区分通用 UI、品牌、用户内容和专用引擎图形。不要把所有 `<svg>` 都认作可替换图标。
2. 在现有共享图标层接入 Lucide，保留对外 props、ref、className、style、aria 和事件契约；没有封装时使用最小共享样式/常量，不引入新的图标运行时。SVG 本来承载 ref/事件时逐项适配，不用 div/span 包装破坏根元素。
3. 静态界面按名称导入，如 `import { Search } from 'lucide-react'`。禁止 `import * as Icons` 后任意字符串查找、全库 re-export、远程 CDN 注入及依赖 DOM 扫描替换的方案；不为常规 UI 引入 Lucide Lab 或第二套图标库。
4. 少量动态业务类型采用明确的具名导入映射，保留原外部 key、持久化值、未知值回退和状态组合。真正开放的动态图标需求才评估官方动态方案，验证 SSR、加载占位、构建体积及失败回退；不要把数据库里的旧图标名全部改写为新名称。
5. 覆盖共享 primitive 内部（关闭、箭头、勾选、加载）、页面、导航及可定制的第三方控件图标槽位。官方 shadcn 源码带来的非 Lucide 图标也做等价视觉适配；不能只迁移显眼的导航就宣称完成。
6. 按语义逐项确认后替换；分批期间允许旧库暂存。所有工作区消费者及专用控件检查后，才删除确实无人引用的旧库/字体/样式和预加载资源。不手动改锁文件，不为去掉旧依赖重写复杂引擎。

## 可访问性与例外

- 装饰图标及已有文字标签旁的图标设为对辅助技术隐藏且不接收键盘焦点。纯图标按钮的本地化名称在按钮/链接上，保留原 title/tooltip 和快捷键；Tooltip 或 SVG 名称不能替代控件名称。不增加 Tab 停靠点或把 SVG 变成点击控件。
- 独立传达信息的图标保留可访问名称与合适角色；按所装版本显式核对 `aria-hidden`，不要让默认隐藏吞掉重要状态，也不要与邻近文字重复播报。
- 品牌 Logo、产品标识、第三方服务商标保留合法来源的原资产、颜色和比例；不拿 Lucide 通用图标替代身份标识，也不因为品牌例外自动安装另一整套通用图标库。用户指定的 HQZY favicon 与控制台蓝色小标志按 [品牌资源规范](brand-assets.md) 定点接入，不套用本文件的方形尺寸、描边或单色规则。
- 用户作品、编辑器内容、图表/地图业务图形、打印/导出图形和不可安全替换的引擎内置图标保留。只有“已有另一个库”不构成例外；须在迁移台账记录位置、原因、范围和验证情况。

## 图标验收

核对所有目标路径的旧库导入、icon font/CSS 图标和自绘通用 SVG 残留；每个剩余项应对应明确例外或待处理项。对真实使用点检查三档尺寸与可见描边、亮/暗主题、禁用/选中/展开/加载、长文字对齐、窄屏及 200% 缩放；检查可访问名称、焦点、点击/键盘回调及 RTL（原本支持时）。

运行受影响包的既有类型检查和构建，确认名称导出、ref、SSR/hydration 和树摇；动态映射改变时验证原 key 与未知值回退。不要新增只匹配图标 class/path 的测试。交付注明 `lucide-outline-v1`、包版本、迁移范围、残留例外及实际检查；仅添加依赖或改了技能文案不算项目完成。
