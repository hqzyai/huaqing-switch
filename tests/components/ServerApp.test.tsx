import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ServerApp } from "@/components/hq/ServerApp";
import { ThemeProvider } from "@/components/theme-provider";
import { endServerSession } from "@/lib/api/hq-server";

const nativeInvoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke: nativeInvoke }));

const snapshot = {
  revision: 2,
  activeId: "private-model",
  connections: [
    {
      id: "private-model",
      name: "Private connection",
      protocol: "openai-chat",
      baseUrl: "https://api.example.invalid/v1",
      model: "example-model",
      apiKeyConfigured: true,
      contextWindow: 1000000,
      maxOutputTokens: 393216,
      supportsImages: false,
    },
  ],
};
const status = {
  running: true,
  address: "http://switch:17621",
  port: 17621,
  error: null,
  activeRequests: 0,
  totalRequests: 0,
};
const json = (value: unknown, code = 200) =>
  new Response(JSON.stringify(value), { status: code });

function renderServer() {
  return render(
    <ThemeProvider syncNativeTheme={false}>
      <ServerApp />
    </ThemeProvider>,
  );
}

describe("Linux browser management authentication", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_HQ_SERVER", "true");
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => {
    endServerSession();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("loads no management data before login, clears submitted credentials and shows authentication failures", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json({ authenticated: false }))
      .mockResolvedValueOnce(json({ error: "管理访问凭证不正确" }, 401));
    vi.stubGlobal("fetch", fetch);
    renderServer();
    const field = await screen.findByLabelText("管理访问凭证");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    fireEvent.change(field, { target: { value: "test-admin-token" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "管理访问凭证不正确",
    );
    expect(field).toHaveValue("");
    expect(JSON.stringify(localStorage)).not.toContain("test-admin-token");
    expect(JSON.stringify(sessionStorage)).not.toContain("test-admin-token");
    expect(nativeInvoke).not.toHaveBeenCalled();
  });

  it("uses the shared connection workspace after login and clears it when logging out", async () => {
    let authenticated = false;
    let secondLogin = false;
    const fetch = vi.fn(async (url: string, options?: RequestInit) => {
      if (url === "/api/session") {
        if (options?.method === "POST") {
          authenticated = true;
          return json({ authenticated });
        }
        if (options?.method === "DELETE") {
          authenticated = false;
          secondLogin = true;
          return json({ authenticated });
        }
        return json({ authenticated });
      }
      if (url.endsWith("hq_get_gateway_status")) return json(status);
      if (url.endsWith("hq_get_connections"))
        return json(
          secondLogin
            ? { revision: 0, activeId: null, connections: [] }
            : snapshot,
        );
      throw new Error("Unexpected management request");
    });
    vi.stubGlobal("fetch", fetch);
    renderServer();
    fireEvent.change(await screen.findByLabelText("管理访问凭证"), {
      target: { value: "first-test-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    await screen.findByRole("article", { name: "Private connection" });
    expect(
      screen.getByRole("region", { name: "模型转发服务" }),
    ).toHaveTextContent("运行中");
    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    expect(
      screen.getByText("Linux 部署版，统一管理 HQ Harness 模型连接与用量。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText("header.windowClose"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
    await screen.findByLabelText("管理访问凭证");
    expect(screen.queryByText("Private connection")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("管理访问凭证"), {
      target: { value: "second-test-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    await screen.findByRole("heading", { name: "添加第一个模型连接" });
    expect(screen.queryByText("Private connection")).not.toBeInTheDocument();
    expect(nativeInvoke).not.toHaveBeenCalled();
  });

  it("returns to login immediately when a management request reports an expired session", async () => {
    const fetch = vi.fn(async (url: string) => {
      if (url === "/api/session") return json({ authenticated: true });
      if (url.endsWith("hq_get_gateway_status")) return json(status);
      return json({ error: "登录已过期" }, 401);
    });
    vi.stubGlobal("fetch", fetch);
    renderServer();
    await screen.findByLabelText("管理访问凭证");
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(nativeInvoke).not.toHaveBeenCalled();
  });

  it("offers a retry for unavailable services and preserves login if server-side logout fails", async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new Error("网络不可用"))
      .mockImplementation(async (url: string, options?: RequestInit) => {
        if (url === "/api/session" && options?.method === "DELETE")
          return json({ error: "暂时无法退出，请重试" }, 503);
        if (url === "/api/session") return json({ authenticated: true });
        return json(url.endsWith("hq_get_gateway_status") ? status : snapshot);
      });
    vi.stubGlobal("fetch", fetch);
    renderServer();
    expect(await screen.findByRole("alert")).toHaveTextContent("网络不可用");
    fireEvent.click(screen.getByRole("button", { name: "重新连接" }));
    await screen.findByRole("article", { name: "Private connection" });
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "暂时无法退出，请重试",
      ),
    );
    expect(
      screen.getByRole("article", { name: "Private connection" }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("管理访问凭证")).not.toBeInTheDocument();
  });
});
