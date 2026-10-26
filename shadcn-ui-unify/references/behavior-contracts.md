# 交互等价规则

按本次涉及的组件阅读。本文件描述必须比对的差异，不是要求新增这些功能。原来没有的行为不要添加。

## 组件映射与陷阱

| 原组件 | 官方候选 | 必须保留的行为；不满足时的处理 |
| --- | --- | --- |
| 按钮、链接按钮 | Button | submit/reset/button、表单归属、链接语义、href/target/download、修饰键打开、preventDefault、loading 时是否禁用。不把链接改为 JS 导航；不嵌套 button/anchor。 |
| Input/Textarea | Input/Textarea | input type、IME composition、onChange/onInput/onBlur 时机、原生校验、autocomplete、ref、光标/selection、数值空值、reset。不把 uncontrolled 改成 controlled。 |
| 原生 select | Native Select | change event、空 option、multiple/size、表单序列化和手机原生选择器。先检查目标源码是否支持全部属性；不满足则保留原生标签并主题化。不直接升级为自定义 Select。 |
| 自定义下拉/搜索选择 | Select/Combobox | 多选、搜索/debounce、远程加载、过滤、清空、值类型、创建条目、关闭条件和键盘。缺任何能力都不降级替换。 |
| Checkbox/Switch/Radio | 对应官方组件 | boolean/indeterminate、event 与 value 回调差异、name/value、required、form reset、label 点击、dirty/touched。三态不能折成两态。 |
| 表单布局/提示 | Field/Label 等当前官方组件 | 原表单库、校验 schema、错误位置及出现时机；不为示例迁移 form/resolver。 |
| Dialog/Modal/Sheet | Dialog/Sheet | open 状态的唯一来源、关闭理由、Esc/遮罩、初始/返回焦点、滚动锁、portal 容器、层叠、嵌套弹窗、关闭后草稿保留。不凭官方默认值推断等价。 |
| 高风险确认框 | Alert Dialog（行为一致时） | 原有取消/确认/关闭方式、异步提交时机、失败后是否保留弹窗。普通 Dialog 不因文案含“删除”就自动升级流程。 |
| Menu/Popover/Tooltip | 对应官方组件 | click/hover/contextmenu、延时、长按、方向键、Tab、禁用项、选择后关闭、焦点恢复、非 modal、遮挡。Tooltip 不替代可操作 Popover。 |
| Tabs/Accordion | Tabs/Accordion | 自动/手动激活、方向、URL 同步、多开/折叠、懒加载、卸载、滚动、跨切换表单状态。 |
| 普通/数据表 | Table + 原数据引擎 | 排序、过滤、分页、行选择、row key、可编辑单元格、固定列、虚拟化、服务端模式。Table 只负责展示；不用 Data Table demo 的本地分页替换服务端逻辑。 |
| Pagination/Breadcrumb/导航 | 对应官方展示组件 | 原 router Link、search/hash、basePath、权限、历史记录、prefetch、修饰键；不新增 Sidebar provider 或快捷键。 |
| 日期/范围选择 | Calendar/Date Picker（能力相符时） | 时区、locale、周起点、禁用日期、范围、时间输入、DST、清空及值格式。不把字符串日期改成 Date 或替换日期库来凑示例。 |
| 通知、进度、占位 | 当前官方 Toast/Progress/Skeleton 等 | 触发时机、去重 ID、持续时间、动作、队列、取消和播报，不重复通知。新动画不得改变完成时机。 |
| 编辑器/图表/上传/拖拽/虚拟列表 | 保留引擎，主题适配 | 工具栏可等价接入官方组件；保留编辑模型、undo/redo、selection、坐标、尺寸测量、导出和上传契约。 |

## 适配层边界

薄适配层可映射视觉 variant、透传原 props/ref、组合官方原语。不能更改业务状态更新、转换后端 DTO、创建第二套状态源或伪造 `ChangeEvent` 再用类型断言掩盖差异。

例：旧 Toggle 的回调直接接受 boolean 时，可在确认官方签名后适配 boolean；旧 Checkbox 消费 `event.target.name`/`checked` 或 `currentTarget` 时，不能简单改成 `onCheckedChange(handler)`。底层回调没有可等价的原生事件时，保留原输入实现并主题化。

保留挂载身份：替换 wrapper、key、条件渲染或 portal 可能重建子树，清空输入与草稿。仅“handler 没变”不能证明行为没变。

## 验证的可观察证据

对涉及的高风险路径记录同一前置状态、同一动作、同一结果：

- 输入中文并提交：IME 未完成时的 Enter 行为、请求次数/载荷与校验提示保持一致。
- 取消再打开表单/弹窗：草稿、默认值、焦点、滚动与迁移前一致；提交失败仍可继续操作。
- 键盘切换标签、选菜单和关闭弹层：焦点轨迹、激活时机、返回焦点与关闭规则一致。
- 表格翻页/排序/筛选：相同请求参数、行数、总数、选择状态及 URL；虚拟滚动无跳跃。
- 同一路由刷新/前进/后退：权限、search/hash、主题及本地状态与原行为一致。

复用既有测试框架和页面对象；只在实际适配风险缺少覆盖时补针对性测试。不更新快照或删断言来掩盖行为回归。原有缺陷单独报告，不默认为本次授权的一部分。
