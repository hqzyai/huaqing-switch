# 间距、尺寸、颜色状态与动效

应用默认 v3 时按本文件映射视觉 tokens。数值以 [neutral-theme.json](../assets/neutral-theme.json) 为准；下文 px 按根字号 16px 换算。这些是本技能的设计决定，不是 shadcn 官方强制规范。

## 尺寸与布局的适用边界

统一字体、语义色、形状和同角色的装饰样式；间距、尺寸也向以下目标收敛，但必须先确认不改变操作路径、信息顺序、已有断点行为和测量契约。

允许调整安全的 padding/gap 和自适应最小尺寸。不重排网格列数、侧栏宽度、页面区块、按钮顺序、滚动容器或粘性区域。不得以“新规范”为理由把表格改成卡片、把 Dialog 改成 Sheet、隐藏次要操作或引入新断点。

有证据表明目标值会破坏交互时，保留最小范围的原几何值并记录例外；不能因“现有项目有样式”就全量豁免。截图变化是换肤的预期结果，操作、可见信息或测量能力变化才是需要处理的兼容问题。

## 间距与对齐

- 间距只取 **0 / 2 / 4 / 6 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48 / 64px**；1px 专用于边框和必要的像素修正，不作布局间距。由内容产生的 auto/fr/% 不需强行量化。
- 页面内边距：桌面 24px，已有窄屏条件下 16px；区块间距 32px。保持原容器宽度和列布局。
- 卡片内边距 20px，窄屏 16px；内部内容组间距 16px。工具栏、并排按钮、图标与文本间距 8px。
- 表单字段间距 16px；label 到控件、控件到帮助/错误文本均为 6px。同一表单按同一基线对齐，不用空格字符定位。
- 弹窗内边距 24px，窄屏 16px；标题区、内容区、操作区之间 20px。
- 使用 `padding-inline`、`margin-inline`、`gap` 等逻辑属性；保留项目已有 RTL 行为。文本与图标视觉居中，数字列按现有对齐方式处理。

## 密度和控件尺寸

默认 **standard**；已有密集工具栏/数据表对应 **compact**；已有触屏样式条件对应 **touch**。同类相邻组件使用同一档，不随页面临时取值，不增加密度切换器或新 JS 设备判断。

| 尺寸目标 | standard | compact | touch |
| --- | --- | --- | --- |
| Button / Input / Select 最小高度 | 36px | 32px | 44px |
| 单行控件水平内边距 | 12px | 8px | 12px |
| 图标按钮最小宽高 | 36px | 32px | 44px |
| 菜单项最小高度 | 32px | 28px | 44px |
| 表格行最小高度 | 44px | 36px | 48px |
| 表格单元格上下内边距 | 10px | 6px | 12px |

这些是最小目标，不是 `height` 硬锁：文字缩放、错误信息、多行内容必须撑开。表格按内容、内边距和现有表格布局计算实际行高，不依赖给 `tr` 加 `min-height`；虚拟列表/表格原有测量值优先，使用原有测量机制重新计算或登记例外。禁止只改 CSS 而不检查滚动跳跃。

- Textarea 最小高度 80px，保留 rows、resize、自动增长、最大长度、滚动和换行规则。
- Checkbox / Radio 可见图形 16px；Switch 轨道 32×18px、滑块 14px。保持至少原命中区域，优先复用原 label 或容器；不扩大透明点击层覆盖邻居。
- Badge 最小高度 22px、左右 6px；头像小/默认/大为 24/32/40px。头像裁切和失败回退沿用原规则，不把品牌 logo 改成圆头像。
- 无受保护宽度约束时，普通 Dialog 目标宽度 480px，已有宽内容 Dialog 为 640px；距视口每边至少 16px。设置可收缩的 max-width，不能覆盖已有全屏、分栏、拖拽调整大小或移动端样式。
- 普通阅读区域最大行宽目标 65ch；只用于现有独立阅读容器，不约束工作台、表格、画布或用户作品预览。容器宽度为产品结构时保留并记录。

## 图标

执行 [Lucide 图标规范](icons.md) 和 JSON 的 `icons`：通用 UI 图标统一为官方 `lucide-react`，尺寸、描边、颜色和语义映射在共享层集中定义。旧库只在迁移期或有具体兼容例外的调用点保留；不能以“优先现有体系”为由跳过图标统一。按钮命中区沿用上方密度规范，不以图标尺寸代替控件尺寸。

## 语义色与状态矩阵

背景/前景成对使用 JSON 的 light/dark token。success / warning / info 只映射已有语义；业务状态、图表系列、代码语法和用户内容保留原配色语义。不得给每张卡片临时分配强调色。

`interaction` 的 hover/active 色值已固定，不再以任意透明度或各处独立调亮方式生成。active 指按下瞬间，selected 指持续选中，不能混用。

| 组件/状态 | 确定的视觉映射 | 必须保留 |
| --- | --- | --- |
| 主按钮 | primary / primary-foreground；hover、active 用 primaryHover、primaryActive，前景不变 | 原先何时为主操作、可用性与动作 |
| 次按钮 | secondary 配对色；hover / active 为 subtleHover / subtleActive | 原操作层级 |
| outline / ghost | 默认分别为 background / transparent；前景 foreground；hover / active 为 outlineHover / outlineActive | outline 边框用 input；ghost 不增加常驻边框 |
| 破坏操作实心按钮 | destructive 配对色；hover / active 为 destructiveHover / destructiveActive | 不自动改变确认流程 |
| 输入与选择控件 | background / foreground，边框 input，placeholder 用 muted-foreground；hover 边框 controlHoverBorder | 原生类型、只读与禁用语义 |
| 持续选中 | selectedBackground / selectedForeground，加原有 indicator，文字 500；有边框时用 ring | aria-selected / checked / pressed 对应原组件语义，不互换 |
| invalid | 边框 destructive，错误文字 destructive；focus 环仍用 ring | 原错误触发时机和描述关联，不用红色代替错误说明 |
| readOnly | 背景 muted、前景 foreground，不套 disabled 透明度 | 可聚焦/复制/选择等原行为 |
| disabled | 组件整体 opacity 0.5；继承对应 variant | 不新增 pointer-events:none、tabIndex、disabled 或 aria-disabled 来替换原机制 |
| loading | 原有忙碌指示使用 currentColor；保留按钮/内容占位宽度 | 原时机、文案、是否禁用、进度/通知及重试逻辑 |

hover 仅针对可用且支持 hover 的指针条件；触屏不依赖 hover 才显示已有操作。CSS 不使用颜色的过渡来驱动状态。focus-visible 与 selected/invalid 可以并存；disabled 不响应视觉 hover/active；不要用后面的宽泛规则覆盖这些状态。禁用元素若原本仍可键盘聚焦，焦点仍须可见。

链接使用 primary，正文内保留常驻下划线，hover 用 2px underline、offset 3px；导航链接保持所属组件样式。若这样改变了既有点击范围或用户内容样式，只作用于第一方应用链接。选择颜色要基于实际背景/透明叠加结果检查，不能只验 token 原色。

## 边框、焦点、阴影与层级

- 普通边框 1px；装饰分隔线用 border，识别控件的边界用 input。选中与聚焦不增加占据布局的边框厚度。
- `focus-visible` 用 ring 色 **2px** 实线 outline，offset **2px**；或视觉等价且不占布局的 ring。offset 填充与所在 surface 相同。不裁掉 outline，不用阴影替代而失去 forced-colors 焦点；在系统高对比下允许系统颜色覆盖。
- Button / Input / Card / sticky 工具栏默认无阴影，靠边框区分；Menu / Popover / Tooltip / Toast 使用 popover 阴影，Dialog / Sheet 使用 modal 阴影。具体明暗模式阴影见 JSON `elevation`；不另造“悬浮卡片”动效。
- 遮罩为 light `rgb(0 0 0 / 0.4)`、dark `rgb(0 0 0 / 0.6)`；默认不加 backdrop blur，不遮住原本不应模态化的区域。
- 层级目标：base 0、sticky 10、popover 20、modalBackdrop 40、modal 50、modal 内浮层 60、toast 80、tooltip 90。**它们不是跨所有 stacking context 可直接替换的绝对值。** 先盘点 portal 与原层叠关系；嵌套弹窗复用现有 stack manager，子浮层必须高于其父层。无法等价映射就保留原 z-index 并登记，不移动 DOM/portal、不增加遮罩、不用 99999 掩盖问题。

## 动效

已有的纯装饰动效目标：颜色/边框/阴影 120ms，透明度与既有浮层过渡 180ms，既有大面板过渡 240ms；easing 使用 JSON `motion`。不使用 `transition: all`，不引入 bounce、额外位移、hover 放大或新的动画库。

只统一确实存在且不参与行为时序的视觉动效；没有动画的控件继续没有。保留 Tooltip/菜单延时、debounce、Toast 停留时间、网络重试、进度时序和重复动画节奏，不把这些替换成视觉 duration。依赖 animationend/transitionend 的卸载、焦点或业务回调必须保留完成机制，否则登记时长例外。

`prefers-reduced-motion: reduce` 下移除非必要位移/缩放和过渡，保留可理解的忙碌/进度反馈；沿用组件已有完成机制，不能让“关闭动画”导致弹层不卸载或任务不完成。不得用全局动画禁用规则影响编辑器、用户作品或第三方引擎。
