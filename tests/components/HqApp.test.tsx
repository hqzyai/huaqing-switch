import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HqApp from "@/components/hq/HqApp";
import type { HqSnapshot } from "@/lib/api/hq";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  status: vi.fn(),
  retryGateway: vi.fn(),
  save: vi.fn(),
  select: vi.fn(),
  remove: vi.fn(),
}));
vi.mock("@/lib/api/hq", () => ({ hqApi: api }));
vi.mock("@/components/theme-provider", () => ({
  useTheme: () => ({ theme: "system", setTheme: vi.fn() }),
}));
vi.mock("@/components/settings/AboutSection", () => ({
  AboutSection: () => <section>Huaqing Switch</section>,
}));
vi.mock("@/components/hq/HqUsagePage", () => ({
  HqUsagePage: () => <section aria-label="HQ 用量">独立 HQ 用量内容</section>,
}));

const existing: HqSnapshot = {
  revision: 4,
  activeId: null,
  connections: [
    {
      id: "hq-1",
      name: "Development API",
      protocol: "openai-responses",
      baseUrl: "https://api.example.invalid/v1",
      model: "model-1",
      apiKeyConfigured: true,
      contextWindow: 32768,
      maxOutputTokens: 4096,
      supportsImages: false,
    },
  ],
};

function renderApp(props: Parameters<typeof HqApp>[0] = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <HqApp {...props} />
    </QueryClientProvider>,
  );
}

describe("HQ-only product workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.status.mockResolvedValue({
      running: true,
      address: "http://127.0.0.1:17621",
      port: 17621,
      error: null,
      activeRequests: 0,
      totalRequests: 0,
    });
    api.get.mockResolvedValue(structuredClone(existing));
  });

  it("shows only HQ connections, HQ usage and settings even with a stored legacy page", async () => {
    localStorage.setItem("hq-model-switch-last-app", "codex");
    localStorage.setItem("hq-model-switch-last-view", "usage");
    renderApp();
    await screen.findByRole("article", { name: "Development API" });
    const nav = within(screen.getByRole("navigation", { name: "主导航" }));
    expect(nav.getAllByRole("button").map((b) => b.textContent)).toEqual([
      "HQ Harness",
      "用量统计",
      "设置",
    ]);
    for (const name of [
      "路由",
      "聚合",
      "Claude Code",
      "Codex",
      "MCP",
      "Skills",
      "授权中心",
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    fireEvent.click(nav.getByRole("button", { name: "用量统计" }));
    expect(screen.getByRole("region", { name: "HQ 用量" })).toBeInTheDocument();
    expect(nav.getByRole("button", { name: "用量统计" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    fireEvent.click(nav.getByRole("button", { name: "设置" }));
    expect(screen.getByRole("heading", { name: "外观" })).toBeInTheDocument();
    expect(screen.queryByText("本地路由")).not.toBeInTheDocument();
    expect(screen.queryByText("应用配置")).not.toBeInTheDocument();
  });

  it("preserves an existing secret while editing and uses the HQ revision", async () => {
    api.save.mockResolvedValue({ ...existing, revision: 5 });
    renderApp();
    fireEvent.click(
      await screen.findByRole("button", { name: "编辑 Development API" }),
    );
    expect(screen.getByLabelText("API Key")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("连接名称"), {
      target: { value: "Renamed API" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
    await waitFor(() =>
      expect(api.save).toHaveBeenCalledWith(
        4,
        expect.objectContaining({
          id: "hq-1",
          name: "Renamed API",
          apiKey: undefined,
        }),
      ),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("offers a state refresh rather than a listener restart in the Linux deployment", async () => {
    api.status.mockResolvedValue({
      running: false,
      address: "http://switch:17621",
      port: 17621,
      error: "服务暂不可用",
      activeRequests: 0,
      totalRequests: 0,
    });
    api.retryGateway.mockResolvedValue({
      running: false,
      error: "服务暂不可用",
    });
    renderApp({ onLogout: vi.fn() });
    const refresh = await screen.findByRole("button", { name: "重新读取状态" });
    await waitFor(() => expect(refresh).toBeEnabled());
    expect(
      screen.queryByRole("button", { name: "重试启动" }),
    ).not.toBeInTheDocument();
    fireEvent.click(refresh);
    await waitFor(() => expect(api.retryGateway).toHaveBeenCalledOnce());
  });

  it("explains raw Token units and saves large provider limits as exact integers", async () => {
    api.save.mockResolvedValue({ ...existing, revision: 5 });
    renderApp();
    fireEvent.click(
      await screen.findByRole("button", { name: "编辑 Development API" }),
    );
    const context = screen.getByLabelText("上下文本地限制（Token）");
    const output = screen.getByLabelText("单次输出上限（Token）");
    expect(context).toHaveAccessibleDescription(/1000000（十进制 1M）/);
    expect(output).toHaveAccessibleDescription(/上限为 393216，就填写 393216/);
    expect(context).toHaveAccessibleDescription(/服务商给出的精确值/);
    fireEvent.change(context, { target: { value: "1000000" } });
    fireEvent.change(output, { target: { value: "393216" } });
    fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
    await waitFor(() =>
      expect(api.save).toHaveBeenCalledWith(
        4,
        expect.objectContaining({
          contextWindow: 1000000,
          maxOutputTokens: 393216,
        }),
      ),
    );
  });

  it("selects a default without calling a legacy mode and confirms deletion", async () => {
    api.select.mockResolvedValue({
      ...existing,
      revision: 5,
      activeId: "hq-1",
    });
    api.remove.mockResolvedValue({
      revision: 6,
      activeId: null,
      connections: [],
    });
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "设为默认" }));
    await waitFor(() => expect(api.select).toHaveBeenCalledWith(4, "hq-1"));
    await screen.findByRole("button", { name: "默认连接" });
    fireEvent.click(
      screen.getByRole("button", { name: "删除 Development API" }),
    );
    expect(api.remove).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("删除后需要重新选择默认连接");
    fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith(5, "hq-1"));
    await screen.findByRole("heading", { name: "添加第一个模型连接" });
  });

  it("keeps a draft open on save failure", async () => {
    api.save.mockRejectedValue("连接配置已发生变化，请刷新后重试");
    renderApp();
    fireEvent.click(
      await screen.findByRole("button", { name: "编辑 Development API" }),
    );
    fireEvent.change(screen.getByLabelText("连接名称"), {
      target: { value: "Unsaved name" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
    await waitFor(() => expect(api.save).toHaveBeenCalled());
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText("连接名称")).toHaveValue("Unsaved name");
  });
});
