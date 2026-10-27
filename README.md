# Huaqing Switch

基于 [CC Switch](https://github.com/farion1231/cc-switch) 的独立品牌桌面应用，项目目录名为 **Huaqing Model Connection**。保留原项目的 Tauri 2、Rust、React 18 和 Tailwind 3 技术栈。

当前应用显示 **HQ Harness 连接管理、用量统计与设置**，路由、聚合、其他客户端以及 MCP/Skills/会话/授权等入口隐藏，托盘只保留 HQ Harness 与退出。旧的可见性设置和导航记录不能重新启用这些入口；原版实现仍保留在源码中。

HQ 连接支持独立保存、编辑、删除与默认选择，列表不会返回已保存的 API Key。HQ-only 启动跳过旧客户端的导入、恢复和扫描。应用启动后在 `127.0.0.1:17621` 提供带鉴权的本机模型服务；HQHarness 自动发现并通过官方 Pi 适配器调用它，不需要填写地址或供应商密钥。支持 OpenAI Chat Completions、Responses 和 Anthropic Messages 的流式调用、工具调用及取消。

使用顺序：启动 Huaqing Switch → 添加供应商连接并设为默认 → 启动 HQHarness 并新建会话。客户端隐藏模型配置，其他直连供应商会被拦截；Switch 不可用时显示明确错误。保存连接不代表上游验证成功。接入契约、运行规则及验收范围见 [HQHarness 接入说明](docs/huaqing-harness-integration.md)。

Windows、macOS 使用桌面客户端；Linux 提供独立的无头服务与 HTTPS 网页管理入口，复用同一套连接、推理和用量实现，不依赖桌面显示服务。部署方式见 [Linux 部署说明](deploy/linux/README.md)，服务配置见 [server/README.md](server/README.md)。

## 开发

使用仓库 `.node-version` 的 Node 22.12.0、`packageManager` 固定的 pnpm 10.12.3，以及 `rust-toolchain.toml` 的 Rust 1.95。各平台仍需 Tauri 的原生构建依赖。

```sh
pnpm install --frozen-lockfile
pnpm typecheck
pnpm test:unit
pnpm build:renderer
pnpm tauri dev
```

`postinstall`、`dev:renderer` 和 `build:renderer` 会从指定的 `shadcn-ui-unify` 标准及锁定字体包生成字体、应用图标和许可证，不依赖运行时远程字体。也可单独执行 `pnpm prepare:brand`。

原生验证：

```sh
CARGO_INCREMENTAL=0 CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 cargo check --manifest-path src-tauri/Cargo.toml --locked
CARGO_INCREMENTAL=0 CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 cargo test --manifest-path src-tauri/Cargo.toml --lib --locked
```

用于实际验证的未签名本机应用包：`pnpm tauri build --debug --bundles app --no-sign`。它不是完成签名、公证和跨平台验收的正式发行包。

## 品牌与数据

- 产品：Huaqing Switch，桌面版本 `0.1.1`，应用 ID `app.hqmodelswitch.desktop`。
- 默认数据：`~/.hq-model-switch/`，数据库 `hq-model-switch.db`。
- 深链：`hqmodelswitch://`；本机代理默认端口 `17621`。
- 自动发现：当前用户主目录下的 `.huaqing-switch/connector.json`，只包含本机访问凭证，不包含供应商密钥。端口冲突会报错并允许重试。
- 原版更新公钥、更新 URL、自动发布和镜像工作流已隔离。未配置自有发行服务时，更新明确不可用，不回退下载原版程序。
- 品牌参数见 `brand.config.json`；规范与验证记录见 `docs/branding-ui-migration.md`。

## 来源与许可

上游基线为 `cb90a2440d1a771d67f46b65b2e0f2ea5106ce85`，来源见 [UPSTREAM.md](UPSTREAM.md)。保留原始 [MIT LICENSE](LICENSE) 和第三方许可。HQZY 图形是用户提供的品牌资产，不声明为 MIT 或 Lucide 资产。

原版说明和发行工作流仅作为来源参考，分别保存在 `docs/upstream-readmes` 和 `docs/upstream-workflows`。
