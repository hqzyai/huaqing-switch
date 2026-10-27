/** Run the installed Team profile against an isolated Switch and deterministic native model fixtures. Never point at production Switch data. */
import assert from "node:assert/strict";
import { createServer } from "node:http";
import {
  mkdtemp,
  mkdir,
  writeFile,
  readFile,
  rm,
  chmod,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes, createHmac } from "node:crypto";
import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { once } from "node:events";

if (process.env.TEAM_ACCEPTANCE_ISOLATED !== "1")
  throw new Error(
    "Use an isolated Switch container and set TEAM_ACCEPTANCE_ISOLATED=1",
  );
const required = (name) => {
  if (!process.env[name]) throw new Error(`Missing fixture setting: ${name}`);
  return process.env[name];
};
const switchAdminURL = required("TEAM_SWITCH_ADMIN_URL");
const switchAdmin = (
  await readFile(required("TEAM_SWITCH_ADMIN_TOKEN_FILE"), "utf8")
).trim();
const switchURL = required("TEAM_SWITCH_URL");
const switchTokenFile = required("TEAM_SWITCH_TOKEN_FILE");
const output = required("TEAM_ACCEPTANCE_OUTPUT");
const fixtureHost = required("TEAM_ACCEPTANCE_FIXTURE_HOST");
const directory = await mkdtemp(join(tmpdir(), "hq-switch-team-installed-"));
const key = () => randomBytes(32).toString("base64url");
const encryptionKey = key(),
  workerKey = key(),
  bootstrap = key(),
  upstreamKey = key();
let childLog = "";
let child,
  fixture,
  workerContext,
  calls = 0,
  cancelled = false,
  userId,
  cookie,
  phase = "setup";
const checks = {};
const protocolPaths = {
  "openai-chat": "chat/completions",
  "openai-responses": "responses",
  anthropic: "messages",
};
const event = (value, name) =>
  `${name ? "event: " + name + "\n" : ""}data: ${JSON.stringify(value)}\n\n`;
const chatBase = {
  id: "chat-fixture",
  object: "chat.completion.chunk",
  created: 1,
  model: "upstream-openai-chat",
};
const call = {
  type: "function_call",
  id: "fixture-tool",
  call_id: "call-fixture",
  name: "fixture",
  arguments: "{}",
  status: "completed",
};
const sse = {
  "openai-chat":
    event({
      ...chatBase,
      choices: [
        {
          index: 0,
          delta: {
            role: "assistant",
            tool_calls: [
              {
                index: 0,
                id: "fixture-tool",
                type: "function",
                function: { name: "fixture", arguments: "{}" },
              },
            ],
          },
          finish_reason: null,
        },
      ],
    }) +
    event({
      ...chatBase,
      choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }],
      usage: {
        prompt_tokens: 10,
        completion_tokens: 5,
        total_tokens: 15,
        prompt_tokens_details: { cached_tokens: 2 },
      },
    }) +
    "data: [DONE]\n\n",
  "openai-responses":
    event(
      {
        type: "response.created",
        response: {
          id: "response-fixture",
          created_at: 1,
          status: "in_progress",
          output: [],
          model: "upstream-openai-responses",
        },
      },
      "response.created",
    ) +
    event(
      {
        type: "response.output_item.added",
        output_index: 0,
        item: { ...call, arguments: "", status: "in_progress" },
      },
      "response.output_item.added",
    ) +
    event(
      {
        type: "response.function_call_arguments.delta",
        item_id: call.id,
        output_index: 0,
        delta: call.arguments,
      },
      "response.function_call_arguments.delta",
    ) +
    event(
      {
        type: "response.function_call_arguments.done",
        item_id: call.id,
        output_index: 0,
        arguments: call.arguments,
      },
      "response.function_call_arguments.done",
    ) +
    event(
      { type: "response.output_item.done", output_index: 0, item: call },
      "response.output_item.done",
    ) +
    event(
      {
        type: "response.completed",
        response: {
          id: "response-fixture",
          created_at: 1,
          status: "completed",
          model: "upstream-openai-responses",
          output: [call],
          usage: {
            input_tokens: 10,
            output_tokens: 5,
            total_tokens: 15,
            input_tokens_details: { cached_tokens: 2 },
            output_tokens_details: { reasoning_tokens: 0 },
          },
        },
      },
      "response.completed",
    ),
  anthropic:
    event(
      {
        type: "message_start",
        message: {
          id: "anthropic-fixture",
          type: "message",
          role: "assistant",
          model: "upstream-anthropic",
          content: [],
          stop_reason: null,
          stop_sequence: null,
          usage: {
            input_tokens: 8,
            output_tokens: 0,
            cache_read_input_tokens: 2,
            cache_creation_input_tokens: 0,
          },
        },
      },
      "message_start",
    ) +
    event(
      {
        type: "content_block_start",
        index: 0,
        content_block: {
          type: "tool_use",
          id: "fixture-tool",
          name: "fixture",
          input: {},
        },
      },
      "content_block_start",
    ) +
    event(
      {
        type: "content_block_delta",
        index: 0,
        delta: { type: "input_json_delta", partial_json: "{}" },
      },
      "content_block_delta",
    ) +
    event({ type: "content_block_stop", index: 0 }, "content_block_stop") +
    event(
      {
        type: "message_delta",
        delta: { stop_reason: "tool_use", stop_sequence: null },
        usage: { output_tokens: 5 },
      },
      "message_delta",
    ) +
    event({ type: "message_stop" }, "message_stop"),
};
const command = async (name, body = {}) => {
  const response = await fetch(`${switchAdminURL}/api/commands/${name}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${switchAdmin}`,
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
  });
  assert.equal(response.status, 200, `Switch fixture command ${name}`);
  return await response.json();
};
const pause = () => new Promise((resolve) => setTimeout(resolve, 100));
async function until(operation, label) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (await operation()) return;
    await pause();
  }
  throw new Error(`Fixture timeout: ${label}`);
}
async function stopChild() {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const done = once(child, "exit");
  child.kill("SIGTERM");
  let timeout;
  await Promise.race([
    done,
    new Promise((resolve) => {
      timeout = setTimeout(resolve, 15000);
    }),
  ]);
  clearTimeout(timeout);
  if (child.exitCode === null && child.signalCode === null) {
    child.kill("SIGKILL");
    await done;
  }
}
try {
  const empty = await command("hq_get_connections");
  assert.deepEqual(
    empty.connections,
    [],
    "Refuse an initialized/production Switch database",
  );
  fixture = createServer((req, res) => {
    void (async () => {
      const body = JSON.parse(
        Buffer.concat(await Array.fromAsync(req)).toString(),
      );
      const protocol = Object.keys(protocolPaths).find((value) =>
        req.url.endsWith("/" + protocolPaths[value]),
      );
      assert.ok(protocol);
      assert.equal(body.model, `upstream-${protocol}`);
      assert.equal(
        req.headers[protocol === "anthropic" ? "x-api-key" : "authorization"],
        protocol === "anthropic" ? upstreamKey : `Bearer ${upstreamKey}`,
      );
      calls++;
      res.setHeader("content-type", "text/event-stream");
      if (body.metadata?.fixture_cancel) {
        res.on("close", () => {
          cancelled = true;
        });
        res.write(
          'data: {"choices":[{"index":0,"delta":{"content":"pending"},"finish_reason":null}],"usage":{"prompt_tokens":10,"completion_tokens":0}}\n\n',
        );
      } else res.end(sse[protocol]);
    })().catch(() => res.destroy());
  });
  fixture.listen(
    Number(process.env.TEAM_ACCEPTANCE_FIXTURE_PORT ?? 19555),
    "0.0.0.0",
  );
  await once(fixture, "listening");
  const upstream = `http://${fixtureHost}:${fixture.address().port}/v1`;
  let snapshot = empty;
  const aliases = [];
  for (const protocol of Object.keys(protocolPaths)) {
    const oldIds = new Set(snapshot.connections.map((item) => item.id));
    snapshot = await command("hq_save_connection", {
      revision: snapshot.revision,
      connection: {
        name: `Fixture ${protocol}`,
        protocol,
        baseUrl: upstream,
        model: `upstream-${protocol}`,
        apiKey: upstreamKey,
        contextWindow: 65536,
        maxOutputTokens: 1024,
        supportsImages: false,
      },
    });
    const added = snapshot.connections.find((item) => !oldIds.has(item.id));
    aliases.push({
      serviceId: `legacy-${protocol}`,
      modelId: `old-${protocol}`,
      connectionId: added.id,
      revision: 1,
      serviceName: `Old ${protocol}`,
      modelName: `Old ${protocol}`,
      enabled: true,
    });
  }
  await mkdir(join(directory, "bin"));
  const docker = join(directory, "bin", "docker");
  await writeFile(
    docker,
    "#!/usr/bin/env node\nconst a=process.argv.slice(2);if(a[0]==='info')console.log('29.9.0');else if(a[0]==='image'&&a[1]==='inspect')console.log('sha256:'+'0'.repeat(64));else if(a[0]==='container'&&a[1]==='ls')console.log('');else process.exit(23)\n",
    { mode: 0o700 },
  );
  // The test container must explicitly permit executing this owned shim from tmpfs.
  assert.equal(
    (await promisify(execFile)(docker, ["info"])).stdout.trim(),
    "29.9.0",
  );
  for (const [name, value] of [
    ["encryption", encryptionKey],
    ["worker", workerKey],
    ["bootstrap", bootstrap],
  ])
    await writeFile(join(directory, name), value, { mode: 0o600 });
  const aliasesPath = join(directory, "aliases.json");
  await writeFile(aliasesPath, JSON.stringify({ version: 1, aliases }), {
    mode: 0o600,
  });
  const reserve = createServer();
  reserve.listen(0, "127.0.0.1");
  await once(reserve, "listening");
  const port = reserve.address().port;
  reserve.close();
  await once(reserve, "close");
  const origin = `http://127.0.0.1:${port}`;
  const env = {
    ...process.env,
    PATH: join(directory, "bin") + ":" + process.env.PATH,
    DSH_HOME: join(directory, "profile"),
    TEAM_PORT: String(port),
    TEAM_BIND_HOST: "127.0.0.1",
    TEAM_PUBLIC_ORIGIN: origin,
    TEAM_DATA_DIR: join(directory, "data"),
    TEAM_ENCRYPTION_KEY_FILE: join(directory, "encryption"),
    TEAM_WORKER_SECRET_FILE: join(directory, "worker"),
    TEAM_BOOTSTRAP_EMAIL: "fixture@example.test",
    TEAM_BOOTSTRAP_TOKEN_FILE: join(directory, "bootstrap"),
    TEAM_WORKER_IMAGE: "fixture:local",
    TEAM_WORKER_GATEWAY_URL: "http://hqzy-control:18411",
    TEAM_GATEWAY_CONTAINER: "",
    TEAM_SWITCH_URL: switchURL,
    TEAM_SWITCH_TOKEN_FILE: switchTokenFile,
    TEAM_SWITCH_ALIASES_FILE: aliasesPath,
    TEAM_SWITCH_CACHE_MS: "0",
    TEAM_SWITCH_MANAGEMENT_URL: "https://switch.fixture.test:18444",
  };
  const launch = () => {
    child = spawn(
      process.execPath,
      [
        "/opt/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js",
        "--profile",
        "team",
      ],
      { env, stdio: ["ignore", "pipe", "pipe"] },
    );
    const capture = (data) => {
      childLog = (childLog + data.toString()).slice(-262144);
    };
    child.stdout.on("data", capture);
    child.stderr.on("data", capture);
  };
  phase = "profile-start";
  launch();
  await until(async () => {
    try {
      return (
        await fetch(origin + "/team/health", {
          signal: AbortSignal.timeout(1000),
        })
      ).ok;
    } catch {
      return false;
    }
  }, "Team profile readiness");
  phase = "account";
  const adminRequest = async (path, body, sessionCookie = cookie) =>
    fetch(origin + "/team/api/" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        origin,
        "content-type": "application/json",
        "x-hqzy-request": "1",
        ...(sessionCookie ? { cookie: sessionCookie } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(15000),
    });
  let response = await adminRequest("accept", {
    token: bootstrap,
    password: "Fixture-only password 2026!",
  });
  assert.equal(response.status, 200);
  response = await adminRequest("login", {
    email: "fixture@example.test",
    password: "Fixture-only password 2026!",
  });
  assert.equal(response.status, 200);
  cookie = response.headers.getSetCookie()[0].split(";")[0];
  const login = await response.json();
  userId = login.user.id;
  const workerToken = createHmac("sha256", Buffer.from(workerKey, "base64url"))
    .update(`worker:${userId}`)
    .digest("base64url");
  const internal = async (path, body, protocol = "openai-chat", signal) =>
    fetch(origin + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        host: "hqzy-control:18411",
        "content-type": "application/json",
        ...(protocol === "anthropic"
          ? { "x-api-key": workerToken }
          : { authorization: `Bearer ${workerToken}` }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal,
    });
  response = await internal("/internal/catalog");
  assert.equal(response.status, 200);
  const catalog = await response.json();
  assert.deepEqual(
    catalog.services.map((item) => item.id).sort(),
    aliases.map((item) => item.serviceId).sort(),
  );
  const catalogText = JSON.stringify(catalog);
  for (const secret of [upstreamKey, workerToken, switchAdmin])
    assert.ok(!catalogText.includes(secret));
  checks.catalogUsesOldSessionIdsWithoutCredentials = true;
  phase = "protocols";
  for (const protocol of Object.keys(protocolPaths)) {
    response = await internal(
      `/internal/models/legacy-${protocol}/v1/${protocolPaths[protocol]}`,
      {
        model: `old-${protocol}`,
        stream: true,
        ...(protocol === "openai-responses"
          ? { input: "fixture", max_output_tokens: 64 }
          : {
              messages: [{ role: "user", content: "fixture" }],
              max_tokens: 64,
            }),
      },
      protocol,
    );
    assert.equal(response.status, 200);
    assert.equal(await response.text(), sse[protocol]);
  }
  checks.threeNativeToolStreamsAndUsage = true;
  phase = "installed-pi-adapters";
  process.env.DSH_TEAM_TOKEN = workerToken;
  process.env.DSH_HOME = join(directory, "worker-profile");
  const composition = join(directory, "worker-acceptance.json");
  await writeFile(
    composition,
    JSON.stringify([
      {
        id: "llm",
        name: "file:///opt/dsh/node_modules/@deepseek-ai/dsh-llm/lib/index.js",
      },
      {
        id: "team-runtime",
        name: "file:///opt/dsh/node_modules/@deepseek-ai/dsh-host-team-runtime/lib/index.js",
        config: { gatewayURL: origin, refreshMs: 1000, timeoutMs: 5000 },
      },
    ]),
  );
  const { boot } = await import(
    "/opt/dsh/node_modules/@deepseek-ai/dsh-app-boot/lib/index.js"
  );
  workerContext = await boot(
    "huaqing-switch-team-acceptance",
    composition,
    [],
    (ctx) => {
      ctx.provide("connection", {
        authenticatedUrl: (value) => `${value}/?token=${"b".repeat(43)}`,
      });
      ctx.provide("webServer", { getOrigin: () => origin });
    },
  );
  for (const protocol of Object.keys(protocolPaths)) {
    const chunks = [];
    for await (const chunk of workerContext.llm.stream({
      provider: `team-legacy-${protocol}`,
      model: `old-${protocol}`,
      messages: [
        {
          role: "user",
          content: [{ type: "text", text: "Call fixture once" }],
        },
      ],
      tools: [
        {
          name: "fixture",
          description: "Deterministic fixture",
          parameters: { type: "object", properties: {} },
        },
      ],
    }))
      chunks.push(chunk);
    assert.ok(
      chunks.some(
        (chunk) => chunk.type === "tool-call-delta" && chunk.name === "fixture",
      ),
    );
    assert.ok(
      !chunks.some(
        (chunk) =>
          chunk.type === "finish" &&
          ["error", "aborted"].includes(chunk.reason.kind),
      ),
    );
    const usage = chunks.find((chunk) => chunk.type === "usage")?.usage;
    assert.ok(usage);
    assert.equal(usage.inputTokens, 8);
    assert.equal(usage.outputTokens, 5);
    assert.equal(usage.cacheReadTokens, 2);
  }
  checks.installedTeamRuntimePiAdaptersParseThreeToolStreams = true;
  await workerContext.fiber.dispose();
  workerContext = undefined;
  phase = "cancellation";
  const controller = new AbortController();
  response = await internal(
    "/internal/models/legacy-openai-chat/v1/chat/completions",
    {
      model: "old-openai-chat",
      stream: true,
      messages: [],
      metadata: { fixture_cancel: true },
    },
    "openai-chat",
    controller.signal,
  );
  assert.equal(response.status, 200);
  const reader = response.body.getReader();
  await reader.read();
  controller.abort();
  await reader.cancel().catch(() => {});
  await until(() => cancelled, "upstream cancellation");
  checks.cancelPropagatesAcrossBothGateways = true;
  phase = "revision-revocation";
  const chat = snapshot.connections.find(
    (item) => item.id === aliases[0].connectionId,
  );
  snapshot = await command("hq_save_connection", {
    revision: snapshot.revision,
    connection: {
      ...chat,
      name: "Edited chat fixture",
      apiKey: undefined,
      apiKeyConfigured: undefined,
    },
  });
  response = await internal(
    "/internal/models/legacy-openai-chat/v1/chat/completions",
    { model: "old-openai-chat", stream: true, messages: [] },
  );
  assert.equal(response.status, 403);
  await response.body?.cancel();
  assert.equal(calls, 7);
  response = await internal("/internal/catalog");
  const changed = await response.json();
  const canonical = changed.services.find(
    (service) => service.id === "switch-" + chat.id,
  );
  assert.ok(canonical);
  response = await internal(
    `/internal/models/${canonical.id}/v1/chat/completions`,
    { model: canonical.models[0].id, stream: true, messages: [] },
  );
  assert.equal(response.status, 200);
  assert.equal(await response.text(), sse["openai-chat"]);
  checks.editedConnectionRevokesOldAliasAndOffersNewPinnedModel = true;
  phase = "account-persistence";
  await stopChild();
  launch();
  await until(async () => {
    try {
      return (
        await fetch(origin + "/team/health", {
          signal: AbortSignal.timeout(1000),
        })
      ).ok;
    } catch {
      return false;
    }
  }, "profile restart");
  response = await adminRequest("me");
  assert.equal(response.status, 200);
  const me = await response.json();
  assert.equal(me.user.id, userId);
  response = await internal("/internal/catalog");
  assert.equal(response.status, 200);
  assert.equal((await response.json()).services.length, 3);
  checks.profileRestartPreservesIdentityAndBrowserSession = true;
  phase = "usage";
  let usage;
  await until(async () => {
    usage = await command("hq_get_usage", { filter: {} });
    return usage.total === 8 && usage.inFlight === 0;
  }, "persistent Switch usage");
  assert.equal(usage.summary.successful, 7);
  assert.equal(usage.summary.cancelled, 1);
  assert.equal(usage.summary.outputTokens, 35);
  assert.equal(calls, 8);
  checks.switchPersistsRealTeamRequests = true;
  const report = {
    passed: true,
    checks,
    fixtureCalls: calls,
    usage: {
      requests: usage.total,
      successful: usage.summary.successful,
      cancelled: usage.summary.cancelled,
      inputTokens: usage.summary.inputTokens,
      outputTokens: usage.summary.outputTokens,
      totalTokens: usage.summary.totalTokens,
    },
    paidModelCall: false,
    productionDataAccessed: false,
    realInstalledTeamProfile: true,
  };
  await writeFile(output, JSON.stringify(report, null, 2) + "\n", {
    mode: 0o600,
  });
  console.log(JSON.stringify(report));
} catch (error) {
  await writeFile(output + ".child.log", childLog, { mode: 0o600 });
  await writeFile(output + ".error.log", String(error?.stack ?? error), {
    mode: 0o600,
  });
  await writeFile(
    output,
    JSON.stringify(
      {
        passed: false,
        phase,
        childExitCode: child?.exitCode ?? null,
        childSignalCode: child?.signalCode ?? null,
        errorCodes: [...new Set(childLog.match(/ERR_[A-Z_]+/g) ?? [])],
        missingPackages: [
          ...childLog.matchAll(
            /Cannot find package ['"](@[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+)['"]/g,
          ),
        ].map((match) => match[1]),
        checks,
        fixtureCalls: calls,
        message: "Installed Team acceptance failed; inspect the declared phase",
        paidModelCall: false,
        productionDataAccessed: false,
      },
      null,
      2,
    ) + "\n",
    { mode: 0o600 },
  );
  process.exitCode = 1;
} finally {
  await workerContext?.fiber.dispose();
  await stopChild();
  if (fixture) {
    fixture.closeAllConnections();
    fixture.close();
    await once(fixture, "close");
  }
  await rm(directory, { recursive: true, force: true });
}
