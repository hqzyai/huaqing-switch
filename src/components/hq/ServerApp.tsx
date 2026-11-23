import { useCallback, useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Loader2, Eye, EyeOff, RefreshCw } from "lucide-react";
import HqApp from "./HqApp";
import {
  endServerSession,
  HqHttpError,
  onServerSessionExpired,
  serverSession,
} from "@/lib/api/hq-server";
import { HuaqingLoginForm } from "@/components/auth/HuaqingLoginForm";
import { Button } from "@/components/ui/button";
import "./hq-auth.css";
import { BRAND } from "@/lib/brand";

type SessionState = "checking" | "anonymous" | "authenticated" | "unavailable";

function LoginForm({ onLogin }: { onLogin: () => void }) {
  return <main className="hq-auth"><div className="hq-auth-layout">
    <header className="hq-auth-heading">
      <img src="/hqzyai-logomark.svg" alt="" width="26" height="22" />
      <span>华清智言 · Huaqing Switch</span>
    </header>
    <section className="hq-auth-card" aria-labelledby="hq-auth-title">
      <h1 id="hq-auth-title">登录</h1>
      <HuaqingLoginForm passwordOnly onLogin={async ({ password }) => { await serverSession.login(password); onLogin(); }} eye={<Eye aria-hidden="true" />} eyeOff={<EyeOff aria-hidden="true" />} />
    </section>
  </div></main>;
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
