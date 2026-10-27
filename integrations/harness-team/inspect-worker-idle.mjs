/** Inspect live Session running flags through the worker's read API; never resume or change a conversation. */
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { createRequire } from "node:module";
import { once } from "node:events";
import { networkInterfaces } from "node:os";
import { randomUUID, createHash } from "node:crypto";
const require = createRequire(
  "/opt/dsh/node_modules/@deepseek-ai/dsh-api-gateway/package.json",
);
const WebSocket = require("ws");
const addresses = Object.values(networkInterfaces()).flat().filter(Boolean);
const ownAddress =
  addresses.find((value) => value.family === "IPv4" && !value.internal)
    ?.address ?? "127.0.0.1";
const origin =
  process.env.TEAM_WORKER_INSPECT_ORIGIN ?? `http://${ownAddress}:3080`;
const url = new URL(origin);
if (
  url.protocol !== "http:" ||
  !["localhost", ...addresses.map((value) => value.address)].includes(
    url.hostname.replace(/^\[|\]$/g, ""),
  ) ||
  url.port !== "3080" ||
  url.pathname !== "/" ||
  url.username ||
  url.password ||
  url.search ||
  url.hash
)
  throw new Error("Use this worker own interface on port 3080");
const token = (
  await readFile(
    join(process.env.DSH_HOME ?? "/home/node/.dsh", ".team-runtime-token"),
    "utf8",
  )
).trim();
if (!/^[A-Za-z0-9_-]{43}$/.test(token))
  throw new Error("Worker readiness token is invalid");
const response = await fetch(
  `${origin}/connect?token=${encodeURIComponent(token)}&interactions=none`,
  { redirect: "manual", signal: AbortSignal.timeout(5000) },
);
await response.body?.cancel();
if (response.status !== 302)
  throw new Error("Worker inspection authentication failed");
const cookie = response.headers
  .getSetCookie()
  .map((value) => value.split(";")[0])
  .join("; ");
if (!cookie)
  throw new Error("Worker inspection authentication omitted a cookie");
const socket = new WebSocket(
  `${origin.replace("http:", "ws:")}/api/remote.mux`,
  { headers: { cookie, origin }, handshakeTimeout: 5000 },
);
try {
  await once(socket, "open");
  const response = await fetch(origin + "/api/session/list", {
    method: "POST",
    headers: { cookie, "content-type": "application/json", origin },
    body: JSON.stringify({
      type: "client-request",
      rpcId: randomUUID(),
      method: "session/list",
      payload: { args: { _request: {} } },
    }),
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error("Live Session inspection was refused");
  const body = await response.json();
  if (process.env.TEAM_WORKER_INSPECT_SCHEMA === "1") {
    const structure = (value, depth = 0) =>
      value === null
        ? "null"
        : Array.isArray(value)
          ? {
              type: "array",
              length: value.length,
              ...(value.length
                ? { entry: structure(value[0], depth + 1) }
                : {}),
            }
          : typeof value === "object"
            ? depth < 4
              ? Object.fromEntries(
                  Object.entries(value)
                    .slice(0, 80)
                    .map(([key, item]) => [key, structure(item, depth + 1)]),
                )
              : "object"
            : typeof value;
    console.log(
      JSON.stringify({
        schema: structure(body),
        errorCode: body.error?.code ?? body.result?.error?.code ?? null,
      }),
    );
  }
  const items = body.result?.ok === true ? body.result.value?.items : undefined;
  if (
    !Array.isArray(items) ||
    items.some(
      (item) =>
        typeof item.running !== "boolean" || typeof item.sessionId !== "string",
    )
  )
    throw new Error("Live Session running flags are unavailable");
  const result = {
    passed: true,
    totalSessions: items.length,
    sessionIdsSha256: createHash("sha256")
      .update(JSON.stringify(items.map((item) => item.sessionId).sort()))
      .digest("hex"),
    runningSessions: items.filter((item) => item.running).length,
    idle: items.every((item) => !item.running),
    source: "live-session/list",
    paidModelCall: false,
    conversationModified: false,
  };
  console.log(JSON.stringify(result));
  if (!result.idle) process.exitCode = 2;
} finally {
  socket.terminate();
}
