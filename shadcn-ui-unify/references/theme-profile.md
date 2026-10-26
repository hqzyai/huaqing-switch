# 统一主题基线

默认读取 [neutral-theme.json](../assets/neutral-theme.json)，其 `id` 为 `shadcn-neutral-v3`。这是本技能自定义的完整视觉基线，采用 shadcn 语义颜色 token 名称，**不宣称是官方 preset 或官方默认值的逐字副本**。v3 保留 v2 的字体、排版、形状和基础配色，补齐布局尺度、状态、组件规范和验收。

## 选择与复用

优先顺序：用户明确指定的统一品牌/基准项目或固定版本 → 本技能当前默认基线及明确记录的用户约束/兼容例外。仅记录过旧版本不等于用户要求固定旧版；本次请求使用新版时，增量迁移到 v3。未指定基准时在不同项目复用同一 JSON，不临时改变主色或圆角。

指定某项目为基准时，从其实际生效的变量、variants 和样式提取，不凭截图猜测交互实现。目标项目因兼容性使用不同官方系列时，通过 tokens/variants 对齐视觉。主题选择写入已有设计文档或迁移台账，保留来源、版本和例外，不建新的运行时配置服务。

重复调用先检查是否已达基线；已符合的部分不重新生成、不重复插入 CSS/provider、不升级组件源码。用户后续的明确样式修改作为新约束，不盲目覆盖。

v3 的图标补充规范单独标识为 `icons.profile = lucide-outline-v1`。保留原排版/配色版本，迁移台账同时记录图标子规范和实际 `lucide-react` 锁定版本；旧 v3 记录不足以证明新的图标规范已实施。

用户指定的品牌资源另记为 `branding.profile = hqzy-logomark-v2`，按 [品牌资源规范](brand-assets.md) 选择和接入。`branding` 保存技能相对资源路径、哈希与展示档位，不是 CSS token、运行时文件路径或 Next.js metadata 对象；其他品牌项目不自动采用这个品牌标志。

## JSON 如何进入项目

`light`/`dark` 的键对应 `--background` 等变量。值是完整 hex 色值，只是跨 CSS 体系的交换格式：

- 消费者是 `var(--background)`：可直接放完整 hex。
- 消费者是 `hsl(var(--background))`：转换成 HSL 分量（如 `#171717` → `0 0% 9.02%`），保持整条 token 链一致。
- 消费者是 `rgb(var(--background) / <alpha-value>)`：转成其需要的 RGB 分量。
- Tailwind v4：在既有 `@theme inline` 按官方方式映射语义 utility；复用项目支持的 radius scale，不复制其他代际公式。
- CSS Modules / CSS-in-JS / 旧库主题：从同一变量映射，避免另写近似色。变量名冲突时按项目规范命名并同步消费端，不覆盖第三方同名含义。

合并到实际 owning stylesheet，遵守 import-only 入口等约束。主题作用域也要覆盖 portal 区域，不可为继承颜色而移动 portal 并改变定位、遮挡或焦点。

JSON 的 `spacing`、`density`、`layout`、`sizes`、`icons`、`focus`、`interaction`、`elevation`、`layers`、`motion` 是技能扩展数据，不是官方 props。`componentRadii` 和 `componentElevation` 是角色到 token 的别名映射。按项目既有 token 命名转成 CSS/主题配置，再在共享组件消费；不把整个 JSON 透传进 DOM 或当成第三方 theme API。尺寸多为 rem，描边/焦点为 px，层级为整数；不要统一粗暴转换单位。

## 视觉规则

- 背景、卡片、浮层用 surface/foreground 成对 token；次级操作用 secondary，hover/选中用 accent，错误/破坏操作用 destructive。
- `radius` 是基础映射入口；实际形状以 JSON 的 `radii` 与 `componentRadii` 为准，逐角色适配所用官方系列。不假定改一个 `--radius` 就能让全部组件自动符合规范。
- 官方组件保留行为实现，视觉按 [间距与状态](layout-and-states.md) 的固定值适配；同类控件复用统一 variants。保留可见焦点、禁用态和语义，不仅用颜色表达状态。
- 字体、字号、字重、行高按 [字体与形状规范](typography-and-shape.md) 和 JSON 的 `typography` 执行。默认自托管 Geist、Noto Sans SC、Geist Mono；系统 fallback 仅用于容错。保留用户内容和编辑器内容自身字体设置，但应用工具栏、菜单、表单须统一。
- 通用 UI 图标使用 Lucide；按 [图标规范](icons.md) 迁移旧库和自绘通用 SVG。`icons.profile`、`library`、`package` 是来源/规范元数据，`strokeScaling` 是适配目标，都不能直接透传给 SVG；依据所装包实际 API 实现尺寸与描边。
- 控件按 standard / compact / touch 三档最小尺寸及固定间距收敛，不用全站固定 height 锁死内容。原容器结构、断点及虚拟滚动测量契约保留；不能等价映射的局部几何登记例外。
- `semantic` 的 success/warning/info 是技能扩展，仅在项目已有该语义时映射，别当作所有官方组件支持的 props。
- `destructive-foreground` 等是否实际被消费需检查源码；部分组件直接用 `text-white` 或暗色类，须适配前景色并检查真实对比度。仅改变量不自动修复所有组合。
- 图表、地图、代码高亮和业务状态颜色须保留原语义与区分度，不强制黑白。未包含 chart 色值是刻意保留业务配色。

至少检验正文/辅助文案/按钮前景背景及实际 focus、disabled、hover、selected、invalid 状态。颜色对比通过不代表布局和键盘已通过。局部品牌或几何差异记录为例外，不报告成完全一致。
