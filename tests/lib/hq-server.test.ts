import { afterEach, describe, expect, it, vi } from "vitest";
import {
  callServerCommand,
  endServerSession,
  HqHttpError,
  onServerSessionExpired,
  serverSession,
} from "@/lib/api/hq-server";

afterEach(() => {
  endServerSession();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("Linux HQ browser transport", () => {
  it.each([
    ["session", 15_000],
    ["command", 30_000],
  ] as const)("bounds an unresponsive %s request", async (kind, deadline) => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, options: RequestInit) =>
          new Promise((_resolve, reject) => {
            options.signal?.addEventListener("abort", () =>
              reject(new Error("aborted")),
            );
          }),
      ),
    );
    const request =
      kind === "session"
        ? serverSession.status()
        : callServerCommand("hq_get_usage");
    const result = expect(request).rejects.toMatchObject({
      status: 408,
      message: "服务响应超时，请重试",
    });
    await vi.advanceTimersByTimeAsync(deadline);
    await result;
  });

  it("uses same-origin cookie authentication and CSRF headers without exposing tokens", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ revision: 3 })));
    vi.stubGlobal("fetch", fetch);
    await expect(
      callServerCommand("hq_select_connection", {
        revision: 2,
        id: "connection",
      }),
    ).resolves.toEqual({ revision: 3 });
    expect(fetch).toHaveBeenCalledWith(
      "/api/commands/hq_select_connection",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Content-Type": "application/json", "X-HQ-CSRF": "1" },
        body: JSON.stringify({ revision: 2, id: "connection" }),
      }),
    );
    const options = fetch.mock.calls[0][1] as RequestInit;
    expect(options.headers).not.toHaveProperty("Authorization");
    expect(options.headers).not.toHaveProperty("Origin");
  });

  it("distinguishes expired sessions from provider or validation errors", async () => {
    const expired = vi.fn();
    const stop = onServerSessionExpired(expired);
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: "连接版本已变化" }), {
          status: 409,
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: "登录已失效" }), { status: 401 }),
      );
    vi.stubGlobal("fetch", fetch);
    await expect(callServerCommand("hq_save_connection")).rejects.toMatchObject(
      { message: "连接版本已变化", status: 409 },
    );
    expect(expired).not.toHaveBeenCalled();
    await expect(callServerCommand("hq_get_connections")).rejects.toMatchObject(
      { message: "登录已失效", status: 401 },
    );
    expect(expired).toHaveBeenCalledOnce();
    stop();
  });

  it("aborts pending commands and rejects an already-read stale response after logout", async () => {
    let resolveBody: (value: unknown) => void = () => undefined;
    const body = new Promise((resolve) => {
      resolveBody = resolve;
    });
    const fetch = vi
      .fn()
      .mockResolvedValue({ ok: true, status: 200, json: () => body });
    vi.stubGlobal("fetch", fetch);
    const request = callServerCommand("hq_get_connections");
    const expired = vi.fn();
    const stop = onServerSessionExpired(expired);
    await Promise.resolve();
    endServerSession();
    const signal = (fetch.mock.calls[0][1] as RequestInit).signal;
    expect(signal?.aborted).toBe(true);
    resolveBody({ connections: [{ name: "previous administrator data" }] });
    await expect(request).rejects.toBeInstanceOf(HqHttpError);
    expect(expired).not.toHaveBeenCalled();
    stop();
  });

  it("login sends credentials only to the session endpoint and logout uses the CSRF guard", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ authenticated: false })),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    await expect(serverSession.status()).resolves.toEqual({
      authenticated: false,
    });
    await serverSession.login("test-credential");
    await serverSession.logout();
    expect(fetch.mock.calls[0]).toEqual([
      "/api/session",
      expect.objectContaining({
        credentials: "same-origin",
        cache: "no-store",
      }),
    ]);
    expect(fetch.mock.calls[1]).toEqual([
      "/api/session",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ token: "test-credential" }),
      }),
    ]);
    expect(fetch.mock.calls[2]).toEqual([
      "/api/session",
      expect.objectContaining({
        method: "DELETE",
        headers: { "Content-Type": "application/json", "X-HQ-CSRF": "1" },
      }),
    ]);
    expect(JSON.stringify(localStorage)).not.toContain("test-credential");
    expect(JSON.stringify(sessionStorage)).not.toContain("test-credential");
  });
});
