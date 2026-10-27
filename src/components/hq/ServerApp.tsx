import { useCallback, useEffect, useState, type FormEvent } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Loader2, LogIn, RefreshCw } from "lucide-react";
import HqApp from "./HqApp";
import {
  endServerSession,
  HqHttpError,
  onServerSessionExpired,
  serverSession,
} from "@/lib/api/hq-server";
import { BRAND } from "@/lib/brand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import logo from "@/assets/branding/hqzyai-logomark.svg";

type SessionState = "checking" | "anonymous" | "authenticated" | "unavailable";

function LoginForm({ onLogin }: { onLogin: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const login = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !token.trim()) return;
    const credential = token.trim();
    // Retain the credential only for this request, never in browser storage.
    setToken("");
    setBusy(true);
    setError(null);
    try {
      await serverSession.login(credential);
      onLogin();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-app p-6 text-fg-1">
      <section className="w-full max-w-md space-y-6 rounded-panel border border-border bg-card p-7 shadow-v7-md">
        <div className="flex items-center gap-3">
          <img src={logo} alt="HQZY" className="h-8 w-8" />
          <div className="space-y-1">
            <h1 className="text-title">{BRAND.productName}</h1>
            <p className="text-caption text-fg-2">
              Linux 部署版 · v{BRAND.version}
            </p>
          </div>
        </div>
        <p className="text-body text-fg-2">登录后管理模型连接与用量统计。</p>
        <form onSubmit={(event) => void login(event)} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="server-admin-token">管理访问凭证</Label>
            <Input
              id="server-admin-token"
              name="credential"
              type="password"
              autoComplete="off"
              required
              maxLength={8192}
              value={token}
              onChange={(event) => setToken(event.target.value)}
              disabled={busy}
              aria-describedby={error ? "server-login-error" : undefined}
              aria-invalid={Boolean(error)}
            />
          </div>
          {error && (
            <p
              id="server-login-error"
              role="alert"
              className="text-body text-danger"
            >
              {error}
            </p>
          )}
          <Button
            type="submit"
            className="w-full"
            disabled={busy || !token.trim()}
          >
            {busy ? (
              <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
            ) : (
              <LogIn aria-hidden="true" className="h-4 w-4" />
            )}
            {busy ? "正在登录…" : "登录"}
          </Button>
        </form>
      </section>
    </main>
  );
}

/** Authentication owns the lifetime of all connection and usage caches. */
export function ServerApp() {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, refetchOnWindowFocus: true },
          mutations: { retry: false },
        },
      }),
  );
  const [state, setState] = useState<SessionState>("checking");
  const [checkAttempt, setCheckAttempt] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);

  const clearSession = useCallback(() => {
    endServerSession();
    void client.cancelQueries();
    client.clear();
    setError(null);
    setState("anonymous");
  }, [client]);

  useEffect(() => onServerSessionExpired(clearSession), [clearSession]);

  useEffect(() => {
    let active = true;
    setState("checking");
    void serverSession
      .status()
      .then((session) => {
        if (active)
          setState(session.authenticated ? "authenticated" : "anonymous");
      })
      .catch((reason) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "无法连接服务");
        setState("unavailable");
      });
    return () => {
      active = false;
    };
  }, [checkAttempt]);

  const logout = async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    setError(null);
    try {
      await serverSession.logout();
      clearSession();
    } catch (reason) {
      if (reason instanceof HqHttpError && reason.status === 401)
        clearSession();
      else
        setError(
          reason instanceof Error ? reason.message : "退出登录失败，请重试",
        );
    } finally {
      setLoggingOut(false);
    }
  };

  if (state === "checking") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-app text-fg-2">
        <p role="status" className="flex items-center gap-2">
          <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
          正在确认登录状态…
        </p>
      </main>
    );
  }
  if (state === "unavailable") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-app p-6 text-fg-1">
        <section className="w-full max-w-md space-y-4 rounded-panel border border-border bg-card p-6">
          <h1 className="text-title">无法连接 {BRAND.productName}</h1>
          <p role="alert" className="text-body text-fg-2">
            {error}
          </p>
          <Button
            variant="outline"
            onClick={() => setCheckAttempt((attempt) => attempt + 1)}
          >
            <RefreshCw aria-hidden="true" className="h-4 w-4" />
            重新连接
          </Button>
        </section>
      </main>
    );
  }
  if (state === "anonymous") {
    return (
      <LoginForm
        onLogin={() => {
          client.clear();
          setState("authenticated");
        }}
      />
    );
  }
  return (
    <QueryClientProvider client={client}>
      {error && (
        <p
          role="alert"
          className="fixed start-1/2 top-16 z-50 max-w-lg -translate-x-1/2 rounded-control border border-danger bg-card p-3 text-body text-danger shadow-v7-md"
        >
          {error}
        </p>
      )}
      <HqApp onLogout={() => void logout()} loggingOut={loggingOut} />
    </QueryClientProvider>
  );
}
