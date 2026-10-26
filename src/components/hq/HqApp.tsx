import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  Server,
  Settings,
  Trash2,
} from "lucide-react";
import {
  hqApi,
  type HqConnection,
  type HqConnectionInput,
  type HqProtocol,
  type HqSnapshot,
} from "@/lib/api/hq";
import { BRAND } from "@/lib/brand";
import {
  DRAG_REGION_ATTR,
  DRAG_REGION_STYLE,
  isMac,
  isWindows,
} from "@/lib/platform";
import { useTheme } from "@/components/theme-provider";
import { AboutSection } from "@/components/settings/AboutSection";
import { WindowControls } from "@/components/shell/WindowControls";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import logo from "@/assets/branding/hqzyai-logomark.svg";

const QUERY_KEY = ["hq", "connections"];
const PROTOCOLS: Record<HqProtocol, string> = {
  "openai-responses": "OpenAI Responses",
  "openai-chat": "OpenAI Chat Completions",
  anthropic: "Anthropic Messages",
};

/** The shipping product mounts this workspace, never the upstream multi-client app. */
export default function HqApp() {
  const [page, setPage] = useState<"connections" | "settings">("connections");
  const [editor, setEditor] = useState<HqConnection | "new" | null>(null);
  const [deleting, setDeleting] = useState<HqConnection | null>(null);
  const queryClient = useQueryClient();
  const { theme, setTheme } = useTheme();
  const gateway = useQuery({
    queryKey: ["hq", "gateway"],
    queryFn: hqApi.status,
    refetchInterval: () => (document.hidden ? false : 3000),
    retry: false,
  });
  const retryGateway = useMutation({
    mutationFn: hqApi.retryGateway,
    onSuccess: (status) => queryClient.setQueryData(["hq", "gateway"], status),
    onError: (error) => toast.error(String(error)),
  });
  const query = useQuery({
    queryKey: QUERY_KEY,
    queryFn: hqApi.get,
    retry: false,
  });
  const mutation = useMutation({
    mutationFn: (operation: () => Promise<HqSnapshot>) => operation(),
    onSuccess: (snapshot) => queryClient.setQueryData(QUERY_KEY, snapshot),
    onError: (error) => {
      toast.error(String(error));
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
  });
  const busy = mutation.isPending;
  const data = query.data;
  const mutate = async (
    operation: () => Promise<HqSnapshot>,
    done?: () => void,
  ) => {
    try {
      await mutation.mutateAsync(operation);
      done?.();
    } catch {
      /* Mutation reports the error; editor drafts remain intact. */
    }
  };
  const navigation = [
    { id: "connections" as const, title: "HQ Harness", Icon: Server },
    { id: "settings" as const, title: "设置", Icon: Settings },
  ];

  return (
    <div className="flex h-screen overflow-hidden bg-app text-body text-fg-1">
      <nav
        aria-label="主导航"
        className="flex w-52 shrink-0 flex-col border-e border-border bg-sidebar p-3"
      >
        <div
          className={cn(
            "mb-7 flex items-center gap-3 px-1",
            isMac() ? "pt-10" : "pt-3",
          )}
          {...DRAG_REGION_ATTR}
          style={DRAG_REGION_STYLE}
        >
          <img
            src={logo}
            alt="HQZY"
            draggable={false}
            className="h-7 w-7 shrink-0 object-contain"
          />
          <span className="text-strong font-semibold leading-5">
            {BRAND.productName}
          </span>
        </div>
        <a
          href="#main-content"
          className="sr-only rounded-control bg-surface p-2 focus:not-sr-only"
        >
          跳到主要内容
        </a>
        <div className="flex flex-1 flex-col gap-1">
          {navigation.map(({ id, title, Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setPage(id)}
              aria-current={page === id ? "page" : undefined}
              className={cn(
                "flex min-h-10 items-center gap-3 rounded-control px-3 text-start focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring",
                page === id
                  ? "bg-selected font-medium"
                  : "text-fg-2 hover:bg-subtle hover:text-fg-1",
                id === "settings" && "mt-auto",
              )}
            >
              <Icon aria-hidden="true" className="h-4 w-4" />
              {title}
            </button>
          ))}
        </div>
        <p className="mt-4 px-3 text-caption text-fg-2">v{BRAND.version}</p>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        <header
          className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-6"
          {...DRAG_REGION_ATTR}
          style={DRAG_REGION_STYLE}
        >
          <h1 className="flex-1 text-title">
            {page === "connections" ? "HQ Harness" : "设置"}
          </h1>
          {isWindows() && (
            <div
              className="flex items-center gap-1"
              style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
            >
              <WindowControls />
            </div>
          )}
        </header>
        <main
          id="main-content"
          className="min-h-0 flex-1 overflow-y-auto p-6"
          tabIndex={-1}
        >
          <div className="mx-auto max-w-4xl space-y-6">
            {page === "settings" ? (
              <>
                <section
                  className="space-y-4 rounded-panel border border-border bg-card p-5"
                  aria-labelledby="appearance-title"
                >
                  <h2
                    id="appearance-title"
                    className="text-strong font-semibold"
                  >
                    外观
                  </h2>
                  <div className="flex items-center justify-between gap-4">
                    <Label htmlFor="hq-theme">主题</Label>
                    <Select
                      value={theme}
                      onValueChange={(value: "system" | "light" | "dark") =>
                        setTheme(value)
                      }
                    >
                      <SelectTrigger id="hq-theme" className="w-44">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="system">跟随系统</SelectItem>
                        <SelectItem value="light">浅色</SelectItem>
                        <SelectItem value="dark">深色</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </section>
                <AboutSection isPortable={false} />
              </>
            ) : (
              <>
                <section
                  aria-label="本地服务"
                  className="space-y-2 rounded-panel border border-border bg-card p-5"
                >
                  <p role="status" className="text-strong font-medium">
                    本地服务：
                    {gateway.data?.running
                      ? "运行中"
                      : gateway.isPending
                        ? "启动中…"
                        : "未就绪"}
                  </p>
                  {gateway.data?.running ? (
                    <>
                      <p className="break-all font-mono text-caption text-fg-2">
                        {gateway.data.address}
                      </p>
                      <p className="text-caption text-fg-2">
                        进行中 {gateway.data.activeRequests} · 累计请求{" "}
                        {gateway.data.totalRequests}
                      </p>
                    </>
                  ) : (
                    <>
                      <p className="text-body text-fg-2">
                        {gateway.data?.error ??
                          (gateway.isError
                            ? "无法读取本地服务状态"
                            : "正在准备 HQ Harness 连接")}
                      </p>
                      <Button
                        variant="outline"
                        disabled={retryGateway.isPending || gateway.isPending}
                        onClick={() => retryGateway.mutate()}
                      >
                        重试启动
                      </Button>
                    </>
                  )}
                  {data && !data.activeId && (
                    <p className="text-body text-fg-2">
                      请添加连接并设为默认，HQ Harness 会自动使用默认模型。
                    </p>
                  )}
                </section>
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div className="space-y-1">
                    <h2 className="text-title">模型 API 连接</h2>
                    <p className="text-body text-fg-2">
                      管理 HQ Harness 的供应商、模型与访问凭证。
                    </p>
                  </div>
                  <Button
                    onClick={() => setEditor("new")}
                    disabled={!data || busy}
                  >
                    <Plus aria-hidden="true" className="h-4 w-4" />
                    添加连接
                  </Button>
                </div>
                {query.isPending && (
                  <p
                    className="flex items-center gap-2 text-fg-2"
                    role="status"
                  >
                    <Loader2
                      aria-hidden="true"
                      className="h-4 w-4 animate-spin"
                    />
                    正在加载连接…
                  </p>
                )}
                {query.isError && (
                  <div
                    role="alert"
                    className="space-y-3 rounded-panel border border-destructive p-5"
                  >
                    <p>无法加载 HQ Harness 连接：{String(query.error)}</p>
                    <Button
                      variant="outline"
                      onClick={() => void query.refetch()}
                      disabled={query.isFetching}
                    >
                      重新加载
                    </Button>
                  </div>
                )}
                {data?.connections.length === 0 && (
                  <div className="flex flex-col items-center gap-3 rounded-panel border border-dashed border-border py-16 text-center">
                    <Server aria-hidden="true" className="h-8 w-8 text-fg-2" />
                    <h3 className="text-strong font-semibold">
                      添加第一个模型连接
                    </h3>
                    <p className="text-body text-fg-2">
                      填写 API 地址、模型 ID 和 API Key。
                    </p>
                    <Button variant="outline" onClick={() => setEditor("new")}>
                      添加连接
                    </Button>
                  </div>
                )}
                <div className="space-y-3">
                  {data?.connections.map((connection) => (
                    <article
                      key={connection.id}
                      aria-label={connection.name}
                      className="space-y-4 rounded-panel border border-border bg-card p-5"
                    >
                      <div className="flex flex-wrap items-start gap-3">
                        <Server
                          aria-hidden="true"
                          className="mt-0.5 h-5 w-5 shrink-0 text-fg-2"
                        />
                        <div className="min-w-0 flex-1 space-y-1">
                          <h3 className="break-words text-strong font-semibold">
                            {connection.name}
                          </h3>
                          <p className="break-all text-body text-fg-2">
                            {connection.baseUrl}
                          </p>
                        </div>
                        {data.activeId === connection.id && (
                          <Badge variant="secondary">
                            <Check
                              aria-hidden="true"
                              className="me-1 h-4 w-4"
                            />
                            默认连接
                          </Badge>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-3 text-caption text-fg-2">
                        <span>{PROTOCOLS[connection.protocol]}</span>
                        <span className="break-all font-mono">
                          {connection.model}
                        </span>
                        <span className="inline-flex items-center gap-1">
                          <KeyRound aria-hidden="true" className="h-4 w-4" />
                          {connection.apiKeyConfigured
                            ? "已保存密钥"
                            : "未设置密钥"}
                        </span>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          variant="outline"
                          size="compact"
                          disabled={busy || data.activeId === connection.id}
                          onClick={() =>
                            void mutate(() =>
                              hqApi.select(data.revision, connection.id),
                            )
                          }
                        >
                          {data.activeId === connection.id
                            ? "默认连接"
                            : "设为默认"}
                        </Button>
                        <Button
                          variant="ghost"
                          size="compact"
                          disabled={busy}
                          onClick={() => setEditor(connection)}
                          aria-label={`编辑 ${connection.name}`}
                        >
                          <Pencil aria-hidden="true" className="h-4 w-4" />
                          编辑
                        </Button>
                        <Button
                          variant="ghost"
                          size="compact"
                          disabled={busy}
                          onClick={() => setDeleting(connection)}
                          aria-label={`删除 ${connection.name}`}
                        >
                          <Trash2 aria-hidden="true" className="h-4 w-4" />
                          删除
                        </Button>
                      </div>
                    </article>
                  ))}
                </div>
              </>
            )}
          </div>
        </main>
      </div>

      <Dialog
        open={editor !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setEditor(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editor === "new" ? "添加连接" : "编辑连接"}
            </DialogTitle>
            <DialogDescription>
              连接仅保存在 Huaqing Switch 中。
            </DialogDescription>
          </DialogHeader>
          {editor && data && (
            <ConnectionForm
              key={editor === "new" ? "new" : editor.id}
              connection={editor === "new" ? undefined : editor}
              busy={busy}
              onCancel={() => setEditor(null)}
              onSave={(input) =>
                mutate(
                  () => hqApi.save(data.revision, input),
                  () => {
                    setEditor(null);
                    toast.success("连接已保存");
                  },
                )
              }
            />
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setDeleting(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除连接</DialogTitle>
            <DialogDescription>
              确定删除「{deleting?.name}」及其保存的凭证？
              {deleting?.id === data?.activeId
                ? "删除后需要重新选择默认连接。"
                : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setDeleting(null)}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => {
                if (deleting && data)
                  void mutate(
                    () => hqApi.remove(data.revision, deleting.id),
                    () => setDeleting(null),
                  );
              }}
            >
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ConnectionForm({
  connection,
  busy,
  onSave,
  onCancel,
}: {
  connection?: HqConnection;
  busy: boolean;
  onSave: (input: HqConnectionInput) => Promise<void>;
  onCancel: () => void;
}) {
  const [name, setName] = useState(connection?.name ?? "");
  const [protocol, setProtocol] = useState<HqProtocol>(
    connection?.protocol ?? "openai-responses",
  );
  const [baseUrl, setBaseUrl] = useState(connection?.baseUrl ?? "");
  const [model, setModel] = useState(connection?.model ?? "");
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [contextWindow, setContextWindow] = useState(
    String(connection?.contextWindow ?? 32768),
  );
  const [maxOutputTokens, setMaxOutputTokens] = useState(
    String(connection?.maxOutputTokens ?? 4096),
  );
  const [supportsImages, setSupportsImages] = useState(
    connection?.supportsImages ?? false,
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void onSave({
      id: connection?.id,
      name,
      protocol,
      baseUrl,
      model,
      apiKey: clearKey ? "" : apiKey || undefined,
      contextWindow: Number(contextWindow),
      maxOutputTokens: Number(maxOutputTokens),
      supportsImages,
    });
  };
  return (
    <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-1">
        <fieldset disabled={busy} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="hq-name">连接名称</Label>
            <Input
              id="hq-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={80}
              autoFocus
              placeholder="例如：开发环境"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="hq-protocol">API 协议</Label>
            <Select
              value={protocol}
              onValueChange={(value: HqProtocol) => setProtocol(value)}
              disabled={busy}
            >
              <SelectTrigger id="hq-protocol">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(PROTOCOLS).map(([value, title]) => (
                  <SelectItem key={value} value={value}>
                    {title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="hq-url">API 基础地址</Label>
            <Input
              id="hq-url"
              type="url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              required
              maxLength={2048}
              placeholder={
                protocol === "anthropic"
                  ? "https://api.anthropic.com"
                  : "https://api.example.com/v1"
              }
              autoCapitalize="none"
              spellCheck={false}
            />
            <p className="text-caption text-fg-2">
              填写服务商提供的基础地址，不含具体推理接口路径。
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="hq-model">模型 ID</Label>
            <Input
              id="hq-model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              required
              maxLength={256}
              autoCapitalize="none"
              spellCheck={false}
              placeholder="服务商提供的模型 ID"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="hq-key">API Key</Label>
            <Input
              id="hq-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              disabled={clearKey}
              maxLength={8192}
              autoComplete="new-password"
              spellCheck={false}
              aria-describedby="hq-key-help"
            />
            <p id="hq-key-help" className="text-caption text-fg-2">
              {connection?.apiKeyConfigured
                ? "留空将保留已保存的密钥。"
                : "无需鉴权的本地服务可留空。"}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="hq-context">上下文本地限制</Label>
              <Input
                id="hq-context"
                type="number"
                min={1024}
                max={2000000}
                step={1}
                required
                value={contextWindow}
                onChange={(e) => setContextWindow(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="hq-output">单次输出上限</Label>
              <Input
                id="hq-output"
                type="number"
                min={1}
                max={Number(contextWindow) || 2000000}
                step={1}
                required
                value={maxOutputTokens}
                onChange={(e) => setMaxOutputTokens(e.target.value)}
              />
            </div>
          </div>
          <p className="text-caption text-fg-2">
            这是客户端使用的限制，请按模型实际能力调整，不代表服务商公布的容量。
          </p>
          <label className="flex min-h-8 items-center gap-2 text-body">
            <input
              type="checkbox"
              checked={supportsImages}
              onChange={(e) => setSupportsImages(e.target.checked)}
            />
            此模型支持图片输入（请先确认服务商支持）
          </label>
          {connection?.apiKeyConfigured && (
            <label className="flex min-h-8 items-center gap-2 text-body">
              <input
                type="checkbox"
                checked={clearKey}
                onChange={(e) => setClearKey(e.target.checked)}
              />
              清除已保存的密钥
            </label>
          )}
        </fieldset>
      </div>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={onCancel}
        >
          取消
        </Button>
        <Button type="submit" disabled={busy}>
          {busy && (
            <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
          )}
          {busy ? "保存中…" : "保存连接"}
        </Button>
      </DialogFooter>
    </form>
  );
}
