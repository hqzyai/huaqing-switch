/** Browser transport for the Linux deployment. Credentials stay in HttpOnly cookies. */
export class HqHttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "HqHttpError";
  }
}

const expiredListeners = new Set<() => void>();
const pendingCommands = new Set<AbortController>();
let sessionGeneration = 0;

export function onServerSessionExpired(listener: () => void): () => void {
  expiredListeners.add(listener);
  return () => expiredListeners.delete(listener);
}

/** Discard in-flight responses as well as cached data when a login ends. */
export function endServerSession(): void {
  sessionGeneration += 1;
  for (const controller of pendingCommands) controller.abort();
  pendingCommands.clear();
}

async function readJson<T>(response: Response): Promise<T> {
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      typeof payload === "string"
        ? payload
        : payload && typeof payload === "object" && "error" in payload
          ? payload.error
          : payload && typeof payload === "object" && "message" in payload
            ? payload.message
            : null;
    throw new HqHttpError(
      typeof message === "string" && message
        ? message
        : response.status === 401
          ? "登录已失效，请重新登录"
          : `服务请求失败（${response.status}）`,
      response.status,
    );
  }
  // The server owns the same DTO contract as the desktop command handlers.
  return payload as T;
}

const mutationHeaders = {
  "Content-Type": "application/json",
  "X-HQ-CSRF": "1",
};

export async function callServerCommand<T>(
  command: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  const generation = sessionGeneration;
  const controller = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 30_000);
  pendingCommands.add(controller);
  try {
    const response = await fetch(`/api/commands/${command}`, {
      method: "POST",
      headers: mutationHeaders,
      credentials: "same-origin",
      cache: "no-store",
      body: JSON.stringify(args),
      signal: controller.signal,
    });
    const payload = await readJson<T>(response);
    if (generation !== sessionGeneration) {
      throw new HqHttpError("登录已结束，请重新登录", 401);
    }
    return payload;
  } catch (error) {
    if (timedOut) throw new HqHttpError("服务响应超时，请重试", 408);
    if (
      error instanceof HqHttpError &&
      error.status === 401 &&
      generation === sessionGeneration
    ) {
      for (const listener of expiredListeners) listener();
    }
    throw error;
  } finally {
    clearTimeout(timeout);
    pendingCommands.delete(controller);
  }
}

async function sessionRequest<T>(options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15_000);
  try {
    return await readJson<T>(
      await fetch("/api/session", {
        credentials: "same-origin",
        cache: "no-store",
        ...options,
        signal: controller.signal,
      }),
    );
  } catch (error) {
    if (controller.signal.aborted) {
      throw new HqHttpError("服务响应超时，请重试", 408);
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

export const serverSession = {
  status: async (): Promise<{ authenticated: boolean }> => {
    const payload = await sessionRequest<{ authenticated: boolean }>();
    if (!payload || typeof payload.authenticated !== "boolean") {
      throw new HqHttpError("服务返回的登录状态无效，请重试", 502);
    }
    return payload;
  },
  login: async (token: string): Promise<void> => {
    await sessionRequest({
      method: "POST",
      headers: mutationHeaders,
      body: JSON.stringify({ token }),
    });
  },
  logout: async (): Promise<void> => {
    await sessionRequest({ method: "DELETE", headers: mutationHeaders });
  },
};
