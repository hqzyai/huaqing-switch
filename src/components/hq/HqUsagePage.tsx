import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Loader2, RefreshCw } from "lucide-react";
import {
  hqApi,
  type HqRequestOutcome,
  type HqUsageFilter,
  type HqUsageSummary,
  type HqUsageSnapshot,
} from "@/lib/api/hq";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const PAGE_SIZE = 50;
const REFRESH_MS = 15_000;
type Range = "today" | "7d" | "30d" | "all";
const RANGES: Record<Range, string> = {
  today: "今天",
  "7d": "最近 7 天",
  "30d": "最近 30 天",
  all: "全部",
};
const OUTCOMES: Record<HqRequestOutcome, string> = {
  success: "成功",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "异常中断",
};
const integer = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });
const timestamp = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});
const tokens = (value: number | null) =>
  value === null ? "—" : integer.format(value);
const duration = (ms: number | null) =>
  ms === null
    ? "—"
    : ms < 1000
      ? `${integer.format(ms)} ms`
      : `${(ms / 1000).toFixed(2)} s`;

function rangeFilter(range: Range): HqUsageFilter {
  if (range === "all") return {};
  const now = new Date();
  const days = range === "today" ? 1 : range === "7d" ? 7 : 30;
  return {
    startTime:
      new Date(
        now.getFullYear(),
        now.getMonth(),
        now.getDate() - days + 1,
      ).getTime() / 1000,
    endTime:
      new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).getTime() /
      1000,
  };
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-control border border-border bg-card p-4">
      <dt className="text-caption text-fg-2">{label}</dt>
      <dd className="mt-2 break-words text-title font-semibold tabular-nums">
        {value}
      </dd>
    </div>
  );
}

function Summary({
  data,
  inFlight,
}: {
  data: HqUsageSummary;
  inFlight: number;
}) {
  return (
    <section aria-labelledby="hq-usage-summary-title" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="hq-usage-summary-title" className="text-strong font-semibold">
          统计概览
        </h2>
        <p role="status" className="text-caption text-fg-2">
          进行中 {integer.format(inFlight)} 个请求
        </p>
      </div>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Metric label="总 Token" value={tokens(data.totalTokens)} />
        <Metric label="输入 Token（含缓存）" value={tokens(data.inputTokens)} />
        <Metric label="输出 Token" value={tokens(data.outputTokens)} />
        <Metric label="缓存读取 Token" value={tokens(data.cacheReadTokens)} />
        <Metric label="缓存写入 Token" value={tokens(data.cacheWriteTokens)} />
        <Metric
          label="缺失用量的请求"
          value={integer.format(data.usageMissing)}
        />
      </dl>
      <dl className="flex flex-wrap gap-x-6 gap-y-3 rounded-control border border-border bg-card p-4 text-caption">
        {[
          ["总请求（已结束）", integer.format(data.requests)],
          ["成功", integer.format(data.successful)],
          ["失败", integer.format(data.failed)],
          ["取消", integer.format(data.cancelled)],
          ["异常中断", integer.format(data.interrupted)],
          ["平均耗时", duration(data.avgDurationMs)],
        ].map(([label, value]) => (
          <div key={label} className="flex items-baseline gap-2">
            <dt className="text-fg-2">{label}</dt>
            <dd className="font-medium tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="text-caption text-fg-2">
        已返回用量 {integer.format(data.usageReported)} /{" "}
        {integer.format(data.requests)} 个请求（完整{" "}
        {integer.format(data.usageFinal)}
        ，部分 {integer.format(data.usagePartial)}）。Token
        仅汇总服务商已返回的字段， “—”表示未上报，不按 0
        估算。输入已含缓存，缓存读取和写入是其中的子集， 不重复相加。
      </p>
    </section>
  );
}

/** An independent HQ history view; no legacy multi-client usage APIs are called. */
export function HqUsagePage({
  serverDeployment = false,
}: {
  serverDeployment?: boolean;
}) {
  const [range, setRange] = useState<Range>("7d");
  const [connectionId, setConnectionId] = useState<string>();
  const [model, setModel] = useState<string>();
  const [offset, setOffset] = useState(0);
  const [visible, setVisible] = useState(() => !document.hidden);
  const [filterOptions, setFilterOptions] = useState<
    Pick<HqUsageSnapshot, "connections" | "models">
  >({ connections: [], models: [] });
  useEffect(() => {
    const changed = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", changed);
    return () => document.removeEventListener("visibilitychange", changed);
  }, []);
  const query = useQuery({
    queryKey: [
      "hq",
      "usage",
      range,
      connectionId ?? null,
      model ?? null,
      offset,
    ],
    // Recompute natural-day boundaries on every refresh, including after midnight.
    queryFn: () =>
      hqApi.usage({
        ...rangeFilter(range),
        connectionId,
        model,
        offset,
        limit: PAGE_SIZE,
      }),
    // Preserve the pager's focus/scroll while changing pages, never across filters.
    placeholderData: (previousData, previousQuery) =>
      previousQuery?.queryKey[2] === range &&
      previousQuery.queryKey[3] === (connectionId ?? null) &&
      previousQuery.queryKey[4] === (model ?? null)
        ? previousData
        : undefined,
    enabled: visible,
    retry: false,
    refetchInterval: visible ? REFRESH_MS : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
  const data = query.data;
  useEffect(() => {
    if (data) {
      setFilterOptions({ connections: data.connections, models: data.models });
    }
  }, [data]);
  // A retention cleanup may make the last visited page disappear.
  useEffect(() => {
    if (data && offset > 0 && offset >= data.total) setOffset(0);
  }, [data, offset]);

  return (
    <div className="min-w-0 space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          <h2 className="text-title">HQ 用量统计</h2>
          <p className="max-w-2xl text-body text-fg-2">
            {serverDeployment
              ? "统计此服务经 Switch 的 HQ Harness 请求，从部署时开始记录，"
              : "统计本机经 Switch 的 HQ Harness 请求，从本次升级开始记录，"}
            升级前的用量不会回填。
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => void query.refetch()}
          disabled={query.isFetching || !visible}
        >
          <RefreshCw
            aria-hidden="true"
            className={`h-4 w-4 ${query.isFetching ? "animate-spin" : ""}`}
          />
          刷新用量
        </Button>
      </div>

      <section
        aria-label="用量筛选"
        className="grid gap-4 rounded-panel border border-border bg-card p-4 sm:grid-cols-3"
      >
        <div className="min-w-0 space-y-2">
          <Label htmlFor="hq-usage-range">日期范围</Label>
          <Select
            value={range}
            onValueChange={(value: Range) => {
              setRange(value);
              setOffset(0);
            }}
          >
            <SelectTrigger id="hq-usage-range">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(RANGES).map(([value, title]) => (
                <SelectItem key={value} value={value}>
                  {title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="min-w-0 space-y-2">
          <Label htmlFor="hq-usage-connection">连接</Label>
          <Select
            value={
              connectionId === undefined ? "all" : `connection:${connectionId}`
            }
            onValueChange={(value) => {
              setConnectionId(value === "all" ? undefined : value.slice(11));
              setOffset(0);
            }}
          >
            <SelectTrigger id="hq-usage-connection">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部连接</SelectItem>
              {filterOptions.connections.map((connection) => (
                <SelectItem
                  key={connection.id}
                  value={`connection:${connection.id}`}
                >
                  {connection.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="min-w-0 space-y-2">
          <Label htmlFor="hq-usage-model">模型</Label>
          <Select
            value={model === undefined ? "all" : `model:${model}`}
            onValueChange={(value) => {
              setModel(value === "all" ? undefined : value.slice(6));
              setOffset(0);
            }}
          >
            <SelectTrigger id="hq-usage-model">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部模型</SelectItem>
              {filterOptions.models.map((name) => (
                <SelectItem key={name} value={`model:${name}`}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </section>

      <p className="text-caption text-fg-2">
        {serverDeployment ? "按浏览器日期筛选" : "按本机日期筛选"}；页面可见时每
        15 秒自动刷新。
        {query.dataUpdatedAt > 0 && (
          <> 上次更新 {timestamp.format(query.dataUpdatedAt)}。</>
        )}
      </p>
      {query.isError && (
        <div
          role="alert"
          className="space-y-2 rounded-panel border border-destructive p-4"
        >
          <p>无法加载 HQ 用量：{String(query.error)}</p>
          <p className="text-caption text-fg-2">
            可点击“刷新用量”重试；已有数据是上次成功读取的结果。
          </p>
        </div>
      )}
      {query.isPending && (
        <p role="status" className="flex items-center gap-2 text-fg-2">
          <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
          正在加载 HQ 用量…
        </p>
      )}
      {data?.storageError && (
        <p role="alert" className="rounded-panel border border-destructive p-4">
          用量记录发生存储异常，统计结果可能不完整。
        </p>
      )}
      {data && (
        <>
          <Summary data={data.summary} inFlight={data.inFlight} />
          {data.total === 0 ? (
            <div
              role="status"
              className="rounded-panel border border-dashed border-border p-10 text-center"
            >
              <h3 className="text-strong font-semibold">
                此范围暂无已结束的 HQ 请求
              </h3>
              <p className="mt-2 text-body text-fg-2">
                调整筛选，或在 HQ Harness 中发起请求后刷新。
              </p>
            </div>
          ) : (
            <>
              <section
                aria-labelledby="hq-usage-groups-title"
                className="min-w-0 space-y-3"
              >
                <h2
                  id="hq-usage-groups-title"
                  className="text-strong font-semibold"
                >
                  连接与模型分组
                </h2>
                <Table className="min-w-[56rem] tabular-nums">
                  <TableCaption>
                    当前筛选范围的全部分组，不受明细分页影响
                  </TableCaption>
                  <TableHeader>
                    <TableRow>
                      <TableHead scope="col">连接 / 模型</TableHead>
                      <TableHead scope="col">请求</TableHead>
                      <TableHead scope="col">
                        成功 / 失败 / 取消 / 中断
                      </TableHead>
                      <TableHead scope="col">输入 Token</TableHead>
                      <TableHead scope="col">输出 Token</TableHead>
                      <TableHead scope="col">缓存读 / 写</TableHead>
                      <TableHead scope="col">缺失用量</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.perConnectionModel.map((group) => (
                      <TableRow
                        key={JSON.stringify([
                          group.connectionId,
                          group.connectionName,
                          group.model,
                          group.protocol,
                        ])}
                      >
                        <TableCell>
                          <p className="max-w-56 break-words font-medium">
                            {group.connectionName}
                          </p>
                          <p className="max-w-56 break-all font-mono text-caption text-fg-2">
                            {group.model}
                          </p>
                        </TableCell>
                        <TableCell>
                          {integer.format(group.summary.requests)}
                        </TableCell>
                        <TableCell>
                          {group.summary.successful} / {group.summary.failed} /{" "}
                          {group.summary.cancelled} /{" "}
                          {group.summary.interrupted}
                        </TableCell>
                        <TableCell>
                          {tokens(group.summary.inputTokens)}
                        </TableCell>
                        <TableCell>
                          {tokens(group.summary.outputTokens)}
                        </TableCell>
                        <TableCell>
                          {tokens(group.summary.cacheReadTokens)} /{" "}
                          {tokens(group.summary.cacheWriteTokens)}
                        </TableCell>
                        <TableCell>
                          {integer.format(group.summary.usageMissing)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </section>
              <section
                aria-labelledby="hq-usage-recent-title"
                className="min-w-0 space-y-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h2
                    id="hq-usage-recent-title"
                    className="text-strong font-semibold"
                  >
                    最近请求明细
                  </h2>
                  <p role="status" className="text-caption text-fg-2">
                    {query.isPlaceholderData ? (
                      "正在切换分页…"
                    ) : (
                      <>
                        第 {offset + 1}–
                        {Math.min(offset + data.recent.length, data.total)}{" "}
                        条，共 {integer.format(data.total)} 条
                      </>
                    )}
                  </p>
                </div>
                <Table className="min-w-[68rem] tabular-nums">
                  <TableCaption>
                    按请求时间倒序；Token 单位为原始整数
                  </TableCaption>
                  <TableHeader>
                    <TableRow>
                      <TableHead scope="col">时间</TableHead>
                      <TableHead scope="col">连接 / 模型</TableHead>
                      <TableHead scope="col">结果</TableHead>
                      <TableHead scope="col">耗时</TableHead>
                      <TableHead scope="col">输入 Token</TableHead>
                      <TableHead scope="col">输出 Token</TableHead>
                      <TableHead scope="col">缓存读 / 写</TableHead>
                      <TableHead scope="col">总 Token</TableHead>
                      <TableHead scope="col">用量</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.recent.map((entry) => (
                      <TableRow key={entry.id}>
                        <TableCell className="whitespace-nowrap">
                          <time
                            dateTime={new Date(
                              entry.startedAt * 1000,
                            ).toISOString()}
                          >
                            {timestamp.format(entry.startedAt * 1000)}
                          </time>
                        </TableCell>
                        <TableCell>
                          <p className="max-w-52 break-words font-medium">
                            {entry.connectionName}
                          </p>
                          <p className="max-w-52 break-all font-mono text-caption text-fg-2">
                            {entry.model}
                          </p>
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              entry.outcome === "failed" ||
                              entry.outcome === "interrupted"
                                ? "destructive"
                                : "secondary"
                            }
                          >
                            {OUTCOMES[entry.outcome]}
                          </Badge>
                          {entry.httpStatus !== null && (
                            <p className="mt-1 text-caption text-fg-2">
                              HTTP {entry.httpStatus}
                            </p>
                          )}
                          {entry.failureCode && (
                            <p className="mt-1 max-w-44 break-all text-caption text-fg-2">
                              {entry.failureCode}
                            </p>
                          )}
                        </TableCell>
                        <TableCell className="whitespace-nowrap">
                          {duration(entry.durationMs)}
                        </TableCell>
                        <TableCell>{tokens(entry.inputTokens)}</TableCell>
                        <TableCell>{tokens(entry.outputTokens)}</TableCell>
                        <TableCell>
                          {tokens(entry.cacheReadTokens)} /{" "}
                          {tokens(entry.cacheWriteTokens)}
                        </TableCell>
                        <TableCell>{tokens(entry.totalTokens)}</TableCell>
                        <TableCell>
                          {entry.usageStatus === "final"
                            ? "完整用量"
                            : entry.usageStatus === "partial"
                              ? "部分用量"
                              : "未返回用量"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                <nav
                  aria-label="请求明细分页"
                  className="flex items-center justify-end gap-3"
                >
                  <Button
                    variant="outline"
                    size="compact"
                    disabled={offset === 0 || query.isFetching}
                    onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  >
                    <ChevronLeft aria-hidden="true" className="h-4 w-4" />
                    上一页
                  </Button>
                  <Button
                    variant="outline"
                    size="compact"
                    disabled={
                      offset + PAGE_SIZE >= data.total || query.isFetching
                    }
                    onClick={() => setOffset(offset + PAGE_SIZE)}
                  >
                    下一页
                    <ChevronRight aria-hidden="true" className="h-4 w-4" />
                  </Button>
                </nav>
              </section>
            </>
          )}
        </>
      )}
    </div>
  );
}
