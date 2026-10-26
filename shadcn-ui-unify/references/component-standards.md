# 组件视觉规范

只读取本次涉及的类别。共享数值见 [间距与状态](layout-and-states.md)、[字体与形状](typography-and-shape.md) 和 [主题 JSON](../assets/neutral-theme.json)；本文件定义它们如何组合。官方候选不是强制替换清单，接入前仍须核对官方源码和 [行为契约](behavior-contracts.md)。

## Button 与操作区

| 原操作语义 | 目标 variant / 外观 |
| --- | --- |
| 当前明确的主要提交/推进动作 | primary 实心 |
| 次要执行动作 | secondary 浅底 |
| 原有边框次级动作、取消 | outline |
| 工具栏轻操作、纯图标操作 | ghost |
| 原来已作为高风险强调的删除/撤销动作 | destructive |
| 真正的跳转或正文链接 | 原链接元素/路由 Link，配 link 外观 |

沿用原操作层级；一个操作组通常维持原有的一个主动作，不为“统一”新增主按钮、降低已有关键操作层级或移动取消/确定顺序。无法从现有行为确定的 variant 保留原语义并记录。

- 字号/行高 14/20、字重 500，圆角 6px，图标 16px，图标与文字间距 8px；尺寸用所属密度档。
- 纯图标按钮使用同档最小正方形命中区。长文案允许随既有布局扩展，不固定宽度后直接裁掉文字；保留原有换行/截断策略。
- 不通过新增 wrapper 改变原表单提交者、disabled、链接修饰键行为；不新增 loading 时禁用策略。既有 spinner、快捷键提示、计数保持可见。

## 表单、输入与选择

- 字段标签用 label 角色，帮助/错误文字用 caption；输入值用 input 或已有触屏条件下的 touchInput。placeholder 用 muted-foreground，不能替代可见标签。
- label—control 间距 6px，提示间距 6px，字段组间距 16px。前后缀图标用 16px；复用原 wrapper 的定位/padding，不能覆盖文本光标、清除按钮或密码管理器入口。
- 控件边框 input，invalid 边框 destructive；错误文本保留原位置和消息，只统一排版及颜色。已有必填标记保留原文案/含义，不额外加 `required`、校验 schema 或表单库。
- 保留 `id`/`htmlFor`、aria-describedby/errormessage、autocomplete、inputMode、type、min/max/step、maxlength 等。只有在不改变交互且关联可以确认时补足漏传的语义属性；既有验证/可访问性缺陷单独登记。
- Checkbox 三态图形必须可区分；Radio 选中点、Switch thumb 位置保留原映射，不能只用颜色区分。可见图形统一，整行 label 的点击范围沿用原实现。
- Native Select 保留操作系统选择界面；自定义 Select/Combobox 的触发器沿用输入框外观，列表沿用菜单规范。多选 Chip 用 4px 圆角，不改变删除键、清除入口、输入法、搜索、选中数量或值类型。
- 日期/时间输入、Calendar 用同一字体、按钮、语义选中/disabled/focus 样式；保留日历网格、locale、周起点、范围端点、时区和选日命中几何。不可仅为套尺寸改写日期算法或替换日期库。
- 上传区只统一边框、已有拖入态、文件条目和按钮；保留拖拽、粘贴、文件类型、大小限制、队列、取消、重试和后台上传行为。

## 导航、Tabs、Accordion

- 导航项文字 14/20、字重 500，图标 20px；菜单型内嵌项图标 16px。未选中用 muted-foreground；选中用 selectedBackground / selectedForeground，保留原 indicator。层级缩进沿用原数据结构和布局，只在已可安全替换的间距上使用 scale。
- Tabs 保留原来是下划线式还是分段式：下划线用 2px ring 色，不让边框增厚挤动布局；分段容器用 surface 圆角 8px，tab 用 control 圆角 6px。不要在迁移中互换两种展示结构。
- Accordion 分隔线用 border，触发器用 label，内容用 body；圆角只作用于原有外框。Chevron 16px，既有旋转的角度、触发和时序契约不变。
- Breadcrumb / Pagination 复用原链接、当前项、省略项、上一页/下一页逻辑；使用 label/caption 角色和统一焦点。不要新增 Sidebar provider、路由同步、默认展开、自动激活或新快捷键。
- 保留既有 sidebar 宽度、折叠点、隐藏策略、sticky 与滚动锚点；不能用官方完整 Sidebar demo 替换产品导航。

## Card、列表、表格与数据

- Card：card / card-foreground、1px border、12px 圆角、默认无阴影；内边距 20px（窄屏 16px），内容组间距 16px。标题 16/24、600；描述用 caption 或正文既有语义。不把整个卡片新增为可点击元素。
- 普通列表项：body/label 角色，已有分隔线用 border；hover 用 accent，选中用语义选中色并保留标记。保留原整行/局部点击范围及多选方式。
- Table：表头 muted 底、muted-foreground、14/20、500；内容 body 14/22、400；单元格左右 12px，上下按密度，行分隔 1px border。最外层已有边框容器用 8px 圆角，内部单元格 0；不新增 overflow:hidden 截断菜单、sticky 列或焦点。
- 表格 hover 使用 accent，selected 使用选中色并保留 checkbox/indicator。原有斑马纹映射为 background/muted，原本没有就不增加。保留列宽、对齐、表头排序入口、固定列、展开行、编辑行和滚动条。
- 数值列可用 tabular-nums，但不更改格式化、舍入、时区、单位、货币、缺失值或排序规则；保留原文本/数字对齐。统计大数字用 metric 28/36、600；小标签用 caption。
- 分页、筛选和批量操作保持原顺序、权限与出现条件。虚拟化的行高、测量缓存和 row key 属于行为；遵守尺寸例外，不强推固定行高或官方 Data Table 示例。
- 图表、地图、画布和代码高亮保留业务图例/内容配色；仅统一外框、工具栏、原有 tooltip 的可配置应用样式。不要重新计算系列颜色或替换渲染引擎。

## Dialog、Sheet、Popover 与 Menu

- Dialog / Sheet 外露角 12px，popover surface/foreground、1px border 和 modal 阴影；固定到视口边缘的角沿用原边缘处理。Dialog 的标题 16/24、600，说明 body，次级帮助 caption。
- Dialog 内边距 24px（窄屏 16px）、分区间距 20px；宽度目标与适用例外见 layout-and-states。Sheet 保留宽度、方向、占屏比例、拖拽和吸附行为，不改成 Dialog。
- Menu / Popover 圆角 8px；菜单容器内边距 4px，条目左右 8px，条目最小高度按密度；菜单项文字 14/20、500，描述/快捷键 12/18、400，分隔线 border。
- 菜单项高亮 accent、前景 foreground；危险条目用 destructive 文字及可辨识的既有图标/文案，不把整组普通菜单染红。嵌套菜单箭头、checkbox、radio indicator 继续表达原状态。
- Tooltip 圆角 6px、上下 6px/左右 8px、caption 12/18、400，popover 配对色与阴影，最大宽度目标 320px、可换行。保留原 placement、collision padding、延迟、关闭和键盘触发；不能把 tooltip 变为含可操作按钮的浮层。
- 复用原 positioning/portal 与可用空间计算；既有浮层边缘留白可以安全归一时用 8px。保留滚动区和标题/操作区是否固定，检查长表单、嵌套下拉、视口边缘和焦点环。
- 关闭按钮和遮罩只统一外观；不增加关闭按钮、点击外部关闭、Escape 拦截、focus trap 或自动聚焦规则。

## 状态反馈与内容边界

| 已有状态/组件 | 视觉规则 | 不能改变 |
| --- | --- | --- |
| Alert / Banner / Toast | 8px 圆角，卡片配对底色、1px border；标题 label、说明 body，语义文字/图标用 success/warning/info/destructive；已有实心语义底色必须配 semantic foreground | 内容、位置、通知队列/持续时间、关闭、重试、播报频率 |
| Badge / Chip | 4px 圆角，caption，500，最小高 22px；中性 secondary 配对色，已有状态映射 semantic 配对色 | 标签文案、计数、过滤和删除动作；不靠颜色独自编码状态 |
| Empty | 既有容器内边距目标 32px；标题 cardTitle、说明 body、既有图标 24px，组间距 16px | 文案、行动入口、空态判定；不添加插画和新引导流程 |
| Loading / Skeleton | 与最终内容对应的原尺寸；muted 底、surface 圆角，保留已有动画 | 不把原 spinner 换成新 skeleton、不增加闪烁/占位分支或改变请求 |
| Progress | primary 进度与 muted 轨道，保留圆角 4px 与原高度兼容；提供原确定/不确定状态 | 真实数值、最大值、事件和完成时机 |
| Error / Offline | 原错误区域使用语义色、可读 body、既有操作用相应 Button | 不吞掉错误细节，不新增重试、跳转或登录策略 |

长文、Markdown、富文本、文件预览和嵌入作品是内容边界。只对第一方应用自己的阅读区使用 longForm；保留用户内容字体、斜体/粗体、代码、图片比例、语义标题、链接、列表与导出样式。不要用全局 `h1`、`button`、`svg`、`iframe *` 选择器把用户作品一起换肤。

滚动条默认保留系统/现有样式，不隐藏、不变细到难操作；图片/object-fit、业务缩略图、品牌 logo、打印/PDF 导出不纳入默认形状替换。已有 resize、拖动手柄和选区都必须继续可见。
