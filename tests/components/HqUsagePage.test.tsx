import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HqUsagePage } from "@/components/hq/HqUsagePage";
import type { HqUsageFilter, HqUsageSnapshot } from "@/lib/api/hq";

const api = vi.hoisted(() => ({ usage: vi.fn() }));
vi.mock("@/lib/api/hq", () => ({ hqApi: api }));

const snapshot: HqUsageSnapshot = {
  summary: {
    requests: 3,
    successful: 1,
    failed: 1,
    cancelled: 1,
    interrupted: 0,
    usageReported: 2,
    usageMissing: 1,
    usagePartial: 1,
    usageFinal: 1,
    inputTokens: 1000,
    outputTokens: 0,
    cacheReadTokens: 400,
    cacheWriteTokens: null,
    totalTokens: 1000,
    durationMs: 1500,
    avgDurationMs: 750,
  },
  total: 3,
  inFlight: 2,
  storageError: false,
  connections: [{ id: "deleted-connection", name: "历史连接（已删除）" }],
  models: ["historic-model", "unknown-model"],
  perConnectionModel: [],
  recent: [
    {
      id: "request-2",
      startedAt: 1_798_400_001,
      connectionId: "deleted-connection",
      connectionName: "历史连接（已删除）",
      connectionRevision: 2,
      model: "historic-model",
      protocol: "openai-responses",
      streamed: true,
      outcome: "failed",
      httpStatus: 502,
      failureCode: "upstream_error",
      durationMs: 1500,
      usageReported: true,
      usageStatus: "partial",
      inputTokens: 1000,
      outputTokens: 0,
      cacheReadTokens: 400,
      cacheWriteTokens: null,
      totalTokens: 1000,
    },
    {
      id: "request-1",
      startedAt: 1_798_400_000,
      connectionId: "deleted-connection",
      connectionName: "历史连接（已删除）",
      connectionRevision: 1,
      model: "unknown-model",
      protocol: "anthropic",
      streamed: true,
      outcome: "interrupted",
      httpStatus: null,
      failureCode: null,
      durationMs: null,
      usageReported: false,
      usageStatus: "unknown",
      inputTokens: null,
      outputTokens: null,
      cacheReadTokens: null,
      cacheWriteTokens: null,
      totalTokens: null,
    },
  ],
};
snapshot.perConnectionModel = [
  {
    connectionId: "deleted-connection",
    connectionName: "历史连接（已删除）",
    model: "historic-model",
    protocol: "openai-responses",
    summary: snapshot.summary,
  },
];

const clients: QueryClient[] = [];
function renderUsage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <HqUsagePage />
    </QueryClientProvider>,
  );
}
async function choose(label: string, name: string) {
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: label }));
  await user.click(await screen.findByRole("option", { name }));
}

describe("HQ-only usage", () => {
  beforeEach(() => {
    api.usage.mockReset();
    api.usage.mockResolvedValue(structuredClone(snapshot));
    vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    Element.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => {
    clients.splice(0).forEach((client) => client.clear());
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows known zeros, partial/unknown usage and immutable historical names without adding caches twice", async () => {
    renderUsage();
    await screen.findByRole("heading", { name: "统计概览" });
    const summary = screen.getByRole("region", { name: "统计概览" });
    expect(
      within(summary).getByText("输入 Token（含缓存）").nextElementSibling,
    ).toHaveTextContent("1,000");
    expect(
      within(summary).getByText("输出 Token").nextElementSibling,
    ).toHaveTextContent("0");
    expect(
      within(summary).getByText("缓存写入 Token").nextElementSibling,
    ).toHaveTextContent("—");
    expect(
      within(summary).getByText("总 Token").nextElementSibling,
    ).toHaveTextContent("1,000");
    expect(summary).toHaveTextContent("进行中 2 个请求");
    expect(summary).toHaveTextContent("完整 1，部分 1");
    expect(summary).toHaveTextContent("不重复相加");
    const recent = screen.getByRole("region", { name: "最近请求明细" });
    expect(recent).toHaveTextContent("历史连接（已删除）");
    expect(recent).toHaveTextContent("部分用量");
    expect(recent).toHaveTextContent("未返回用量");
    expect(recent).toHaveTextContent("异常中断");
    const unknown = within(recent).getAllByRole("row")[2];
    expect(within(unknown).getAllByText("—")).toHaveLength(4);
    expect(unknown).not.toHaveTextContent("0 ms");
    expect(screen.getByText(/升级前的用量不会回填/)).toBeInTheDocument();
  });

  it("sends raw epoch-second calendar bounds and applies all date presets", async () => {
    renderUsage();
    await screen.findByRole("heading", { name: "统计概览" });
    const today = new Date();
    const boundary = (day: number) =>
      new Date(today.getFullYear(), today.getMonth(), day).getTime() / 1000;
    expect(api.usage).toHaveBeenLastCalledWith(
      expect.objectContaining({
        startTime: boundary(today.getDate() - 6),
        endTime: boundary(today.getDate() + 1),
        offset: 0,
        limit: 50,
      }),
    );
    await choose("日期范围", "今天");
    await waitFor(() =>
      expect(api.usage).toHaveBeenLastCalledWith(
        expect.objectContaining({ startTime: boundary(today.getDate()) }),
      ),
    );
    await choose("日期范围", "最近 30 天");
    await waitFor(() =>
      expect(api.usage).toHaveBeenLastCalledWith(
        expect.objectContaining({ startTime: boundary(today.getDate() - 29) }),
      ),
    );
    await choose("日期范围", "全部");
    await waitFor(() =>
      expect(api.usage).toHaveBeenLastCalledWith({
        connectionId: undefined,
        model: undefined,
        offset: 0,
        limit: 50,
      }),
    );
  });

  it("filters deleted connections and models, paginates, and resets the offset when filters change", async () => {
    api.usage.mockImplementation(async (filter: HqUsageFilter) => ({
      ...structuredClone(snapshot),
      total: 51,
      recent: [
        {
          ...snapshot.recent[0],
          id: `request-${filter.offset}`,
          model: filter.offset === 50 ? "last-page-model" : "historic-model",
        },
      ],
    }));
    renderUsage();
    await screen.findByRole("heading", { name: "最近请求明细" });
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("last-page-model");
    expect(api.usage).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 50, limit: 50 }),
    );
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
    await choose("连接", "历史连接（已删除）");
    await waitFor(() =>
      expect(api.usage).toHaveBeenLastCalledWith(
        expect.objectContaining({
          connectionId: "deleted-connection",
          offset: 0,
        }),
      ),
    );
    await choose("模型", "unknown-model");
    await waitFor(() =>
      expect(api.usage).toHaveBeenLastCalledWith(
        expect.objectContaining({
          connectionId: "deleted-connection",
          model: "unknown-model",
          offset: 0,
        }),
      ),
    );
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  });

  it("shows initial errors and recovers through an explicit refresh", async () => {
    api.usage.mockRejectedValueOnce(new Error("本地统计不可用"));
    renderUsage();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "本地统计不可用",
    );
    fireEvent.click(screen.getByRole("button", { name: "刷新用量" }));
    await screen.findByRole("heading", { name: "统计概览" });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("preserves the pager while loading a page but never shows old results for new filters", async () => {
    api.usage.mockResolvedValue({ ...snapshot, total: 51 });
    renderUsage();
    await screen.findByRole("heading", { name: "最近请求明细" });
    let resolve!: (value: HqUsageSnapshot) => void;
    api.usage.mockReturnValue(
      new Promise<HqUsageSnapshot>((done) => {
        resolve = done;
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(
      screen.getByRole("heading", { name: "最近请求明细" }),
    ).toBeInTheDocument();
    expect(screen.getByText("正在切换分页…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
    await choose("连接", "历史连接（已删除）");
    expect(
      screen.queryByRole("heading", { name: "最近请求明细" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("正在加载 HQ 用量…")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "连接" })).toHaveTextContent(
      "历史连接（已删除）",
    );
    resolve(structuredClone(snapshot));
    await screen.findByRole("heading", { name: "最近请求明细" });
  });

  it("shows an empty state with unknown token totals rather than estimated zeros", async () => {
    api.usage.mockResolvedValue({
      ...snapshot,
      total: 0,
      recent: [],
      perConnectionModel: [],
      summary: {
        ...snapshot.summary,
        requests: 0,
        successful: 0,
        failed: 0,
        cancelled: 0,
        interrupted: 0,
        usageReported: 0,
        usageMissing: 0,
        usagePartial: 0,
        usageFinal: 0,
        inputTokens: null,
        outputTokens: null,
        cacheReadTokens: null,
        cacheWriteTokens: null,
        totalTokens: null,
        durationMs: 0,
        avgDurationMs: null,
      },
    });
    renderUsage();
    await screen.findByRole("heading", { name: "此范围暂无已结束的 HQ 请求" });
    const summary = screen.getByRole("region", { name: "统计概览" });
    expect(
      within(summary).getByText("输入 Token（含缓存）").nextElementSibling,
    ).toHaveTextContent("—");
    expect(
      within(summary).getByText("平均耗时").nextElementSibling,
    ).toHaveTextContent("—");
    expect(
      screen.queryByRole("navigation", { name: "请求明细分页" }),
    ).not.toBeInTheDocument();
  });

  it("keeps data visible while warning about incomplete storage", async () => {
    api.usage.mockResolvedValue({ ...snapshot, storageError: true });
    renderUsage();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "统计结果可能不完整",
    );
    expect(
      screen.getByRole("heading", { name: "最近请求明细" }),
    ).toBeInTheDocument();
  });

  it("refreshes only while visible and stops observing after leaving the page", async () => {
    vi.useFakeTimers();
    const view = renderUsage();
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(api.usage).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(api.usage).toHaveBeenCalledTimes(2);
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    fireEvent(document, new Event("visibilitychange"));
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(api.usage).toHaveBeenCalledTimes(2);
    vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    fireEvent(document, new Event("visibilitychange"));
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(api.usage).toHaveBeenCalledTimes(3);
    view.unmount();
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(api.usage).toHaveBeenCalledTimes(3);
  });
});
