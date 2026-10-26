# shadcn 官方接入规则

编写本技能时于 2026-10-12 核对以下官方入口。使用时重新确认与目标项目版本相符的文档；下列记录不是永久 API 保证。

## 官方入口

- [Introduction](https://ui.shadcn.com/docs)：组件源码分发与本地所有权。
- [Installation](https://ui.shadcn.com/docs/installation)：选择实际框架的 Existing Project 路线。
- [Vite](https://ui.shadcn.com/docs/installation/vite)：已有 Vite 项目的接入步骤；其他框架从 Installation 进入。
- [CLI](https://ui.shadcn.com/docs/cli)：命令及当前选项。
- [components.json](https://ui.shadcn.com/docs/components-json)：配置、alias、CSS 和源码落点。
- [Theming](https://ui.shadcn.com/docs/theming)：语义 tokens 与 Tailwind 映射。
- [Components](https://ui.shadcn.com/docs/components)：从这里选择目标实现系列的具体文档，不猜 URL。
- [Native Select](https://ui.shadcn.com/docs/components/base/native-select)：官方区分原生与自定义选择器。
- [官方源码与许可](https://github.com/shadcn-ui/ui/blob/main/LICENSE.md)：核对时为 MIT；引入源码时保留所需版权和许可声明，依赖许可另行核对。

## 核对方式

先读本地 `components.json`、源码和 lockfile，再对照官方当前/历史文档与对应版本源码。文档不同页面可能针对不同代际，不能把示例中的 `new-york`、`base-nova` 或默认 preset 当成通用配置直接覆盖。

核对时 CLI 文档列出多种底层实现（base、radix、aria）。已有项目保持原系列；未接入项目选符合当前 React 版本和交互要求的一种官方系列。视觉统一不必强迫所有项目更换底层实现。核对 peer dependencies 和 ref 处理；不把依赖 React 新 API 的源码直接放进 React 18。

检查本地 registry，确认 `@shadcn` 没有被改指向第三方。官网列出的社区资源不是本任务的源码来源。必要时追踪组件本身的第三方底层依赖，但不引入其他第三方 UI 模板。

## CLI 操作参考

以下是核对时官方提供的 pnpm 语法，不是对任何项目无条件执行的脚本。沿用项目包管理器，通过当前 CLI help 确认选项。一个迁移批次解析一次实际 CLI 版本，后续使用该版本，记录 lockfile 与源码来源，避免在同批次多次浮动拉取 latest。

```sh
pnpm dlx shadcn@latest info
pnpm dlx shadcn@latest view button input dialog
pnpm dlx shadcn@latest add button --dry-run
pnpm dlx shadcn@latest add button --diff
pnpm dlx shadcn@latest add button
```

`--dry-run`/`--diff` 只在所选版本支持时使用；它们不证明运行行为。不需要新增的组件不要安装。

初始化会改文件和依赖。先确定 app/shared UI 所属目录；已有配置时不要重复 init，尤其不要用新项目 scaffold、`--defaults` 或 `--reinstall` 替换现有工程。需要初始化但影响不明确时，在隔离临时目录查看输出，再合并所需差异。

教程有时为新工程简写为“替换全部 CSS/配置”。在已有项目中只合并必需项：保留 import 顺序、样式所有权、别名、主题 selector、PostCSS/Vite 插件和 RSC 边界。不给整棵 Next.js 页面树加 `use client`。

## Tailwind 与 CSS

- 已用 Tailwind：保留大版本，核对对应组件和官方历史路线。不只安装最新组件再靠类型忽略消除错误。
- 未用 Tailwind：做最小接入，分析 Preflight/全局 reset、cascade layer、扫描范围及顺序对旧 UI 的影响。需要时用对应版本支持的方式隔离新样式；不用全站 `!important` 解决。
- v4 的完整色值变量通常通过 `@theme inline` 暴露；v3 常用 config 与 `hsl(var(--token))`。检查消费方式后转换色值，不把完整 hex/oklch 放进 HSL 分量槽。
- monorepo 的扫描、CSS 入口及 exports 要覆盖共享 UI 包，遵守归属。默认 `components/ui` 不是绕过共享包的理由。

官方源码可作有限适配，保留来源与必要许可。适配改变交互实现时必须验证；不得把自行重写的复杂组件标注为未修改的官方组件。

## 图标接入

官方 shadcn 样例中的图标也遵守本技能的 [Lucide 图标规范](icons.md)。样例若使用其他图标源，在保持组件结构、状态与交互的前提下换成语义相同的 Lucide 图标，记录为视觉适配。使用 CLI 时，先检查所选版本的官方配置 schema；支持图标库配置时选择 Lucide，不支持时在生成后的本地源码中适配，不能编造 CLI 参数或覆盖组件配置来强行切换。新增组件后复查依赖和导入，避免重新带入另一套通用图标库。
