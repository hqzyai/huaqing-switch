import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, writeFile, chmod, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { once } from "node:events";
import {
  SwitchCatalog,
  parseSwitchService,
  switchProviderProfile,
  prepareSwitchInference,
  switchInferenceHeaders,
  loadSwitchCatalog,
  workerCapability,
} from "../index.mjs";

const connection = "11111111-1111-4111-8111-111111111111";
const model = (endpoint, protocol = "openai-chat", revision = 1) => ({
  id: `hq-${connection}-v${revision}`,
  name: "Configured model",
  protocol,
  baseUrl: `${endpoint}/hq/connections/${connection}/${revision}${protocol === "anthropic" ? "" : "/v1"}`,
  contextWindow: 1000000,
  maxOutputTokens: 393216,
  supportsImages: true,
});
const manifest = (models) => ({
  version: 1,
  product: "huaqing-switch",
  models,
  defaultModel: models[0]?.id ?? null,
});
const aliases = {
  version: 1,
  aliases: [
    {
      serviceId: "legacy-provider",
      serviceName: "Existing provider",
      modelId: "original-model",
      modelName: "Original model",
      connectionId: connection,
      revision: 1,
      enabled: true,
      thinkingFormat: "deepseek",
    },
  ],
};
const token = "inference-key".repeat(6);

async function httpFixture(t, handler) {
  const server = createServer((req, res) => {
    void handler(req, res).catch(() => res.destroy());
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    server.closeAllConnections();
    server.close();
    await once(server, "close");
  });
  return `http://127.0.0.1:${server.address().port}`;
}

test("preserves old session identifiers and forwards native protocol streams with only service credentials", async (t) => {
  let protocol = "openai-chat",
    revision = 1,
    calls = 0,
    observed;
  const endpoint = await httpFixture(t, async (req, res) => {
    assert.equal(req.headers.authorization, `Bearer ${token}`);
    assert.equal(req.headers.cookie, undefined);
    if (req.url === "/hq/manifest") {
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify(manifest([model(endpoint, protocol, revision)])));
      return;
    }
    calls++;
    observed = {
      url: req.url,
      headers: req.headers,
      body: JSON.parse(Buffer.concat(await Array.fromAsync(req)).toString()),
    };
    res.setHeader("content-type", "text/event-stream");
    res.write('data: {"tool":"fixture","usage":{"input_tokens":10}}\n\n');
    res.end('data: {"done":true,"usage":{"output_tokens":5}}\n\n');
  });
  for (protocol of ["openai-chat", "openai-responses", "anthropic"]) {
    revision++;
    const catalog = new SwitchCatalog({
      endpoint,
      token,
      aliases: { version: 1, aliases: [{ ...aliases.aliases[0], revision }] },
      ttlMs: 0,
    });
    t.after(() => catalog.close());
    const [service] = await catalog.list();
    assert.equal(service.id, "legacy-provider");
    assert.equal(service.models[0].id, "original-model");
    const profile = switchProviderProfile(
      service,
      "http://control.invalid:18411",
    );
    assert.equal(profile.apiKeyEnv, "DSH_TEAM_TOKEN");
    assert.equal(
      profile.api,
      {
        "openai-chat": "openai-completions",
        "openai-responses": "openai-responses",
        anthropic: "anthropic-messages",
      }[protocol],
    );
    assert.equal(
      profile.baseURL,
      `http://control.invalid:18411/internal/models/legacy-provider${protocol === "anthropic" ? "" : "/v1"}`,
    );
    const route = await catalog.route(
      "legacy-provider",
      "original-model",
      protocol,
    );
    const body = prepareSwitchInference(route, {
      model: "original-model",
      messages: [],
      max_tokens: 12,
    });
    assert.equal(body.model, `hq-${connection}-v${revision}`);
    const response = await fetch(route.url, {
      method: "POST",
      headers: switchInferenceHeaders(route, {
        authorization: "Bearer worker-secret",
        cookie: "browser-secret",
        "anthropic-version": "2023-06-01",
      }),
      body: JSON.stringify(body),
    });
    assert.equal(
      await response.text(),
      'data: {"tool":"fixture","usage":{"input_tokens":10}}\n\ndata: {"done":true,"usage":{"output_tokens":5}}\n\n',
    );
    assert.equal(
      observed.url,
      `/hq/connections/${connection}/${revision}/v1/${{ "openai-chat": "chat/completions", "openai-responses": "responses", anthropic: "messages" }[protocol]}`,
    );
    assert.equal(observed.headers["anthropic-version"], "2023-06-01");
    assert.equal(
      await catalog.route(
        "legacy-provider",
        "original-model",
        protocol === "anthropic" ? "openai-chat" : "anthropic",
      ),
      undefined,
    );
  }
  assert.equal(calls, 3);
});

test("cache commits complete revisions and fails closed after expiration instead of reusing a stale directory", async () => {
  const endpoint = "http://switch.test:17621";
  let bad = false,
    revision = 1,
    requests = 0;
  const catalog = new SwitchCatalog({
    endpoint,
    token,
    ttlMs: 0,
    fetcher: async () => {
      requests++;
      await new Promise((resolve) => setTimeout(resolve, 10));
      return bad
        ? new Response("unavailable", { status: 503 })
        : Response.json(manifest([model(endpoint, "openai-chat", revision)]));
    },
  });
  try {
    const [left, right] = await Promise.all([catalog.list(), catalog.list()]);
    assert.deepEqual(left, right);
    assert.equal(requests, 1);
    revision = 2;
    const [service] = await catalog.list();
    assert.equal(service.models[0].id, `hq-${connection}-v2`);
    assert.equal(
      await catalog.route(service.id, `hq-${connection}-v1`, "openai-chat"),
      undefined,
    );
    bad = true;
    await assert.rejects(catalog.list(), /directory unavailable/);
    await assert.rejects(
      catalog.route(service.id, service.models[0].id, "openai-chat"),
      /directory unavailable/,
    );
  } finally {
    catalog.close();
  }
});

test("rejects route redirection, oversized discovery, malformed fields and new requests after disposal", async () => {
  const endpoint = "http://switch.test:17621";
  for (const body of [
    manifest([{ ...model(endpoint), baseUrl: "https://attacker.invalid/v1" }]),
    manifest([{ ...model(endpoint), id: "hq-undefined-v1" }]),
    manifest([{ ...model(endpoint), maxOutputTokens: 2000001 }]),
  ]) {
    const catalog = new SwitchCatalog({
      endpoint,
      token,
      fetcher: async () => Response.json(body),
    });
    try {
      await assert.rejects(catalog.list(), /directory unavailable/);
    } finally {
      catalog.close();
    }
  }
  const catalog = new SwitchCatalog({
    endpoint,
    token,
    maxBytes: 1024,
    fetcher: async () => new Response("x".repeat(2048)),
  });
  await assert.rejects(catalog.list(), /directory unavailable/);
  catalog.close();
  await assert.rejects(catalog.list(), /directory unavailable/);
  assert.throws(() =>
    parseSwitchService({
      provider: "huaqing-switch",
      protocol: "openai-chat",
      name: "x",
      enabled: true,
      baseURL: "managed-by-huaqing-switch",
      models: [{ name: "x", contextWindow: 1024, maxTokens: 1, vision: false }],
    }),
  );
});

test("missing and disabled legacy connections are revoked rather than silently becoming direct cloud routes", async () => {
  const endpoint = "http://switch.test:17621";
  let entries = [model(endpoint)];
  const catalog = new SwitchCatalog({
    endpoint,
    token,
    ttlMs: 0,
    aliases: {
      version: 1,
      aliases: [{ ...aliases.aliases[0], enabled: false }],
    },
    fetcher: async () => Response.json(manifest(entries)),
  });
  try {
    assert.deepEqual(await catalog.list(), []);
    assert.equal(
      await catalog.route("legacy-provider", "original-model", "openai-chat"),
      undefined,
    );
    entries = [];
    assert.deepEqual(await catalog.list(), []);
  } finally {
    catalog.close();
  }
});

test("loads private deployment credentials and keeps legacy cloud mode when integration is absent", async (t) => {
  assert.equal(await loadSwitchCatalog({}), undefined);
  await assert.rejects(
    loadSwitchCatalog({ TEAM_SWITCH_TOKEN_FILE: "/not-used" }),
  );
  const directory = await mkdtemp(join(tmpdir(), "hq-switch-team-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const path = join(directory, "inference-token");
  await writeFile(path, token, { mode: 0o600 });
  const catalog = await loadSwitchCatalog({
    TEAM_SWITCH_URL: "http://switch.test:17621",
    TEAM_SWITCH_TOKEN_FILE: path,
  });
  catalog.close();
  await chmod(path, 0o644);
  await assert.rejects(
    loadSwitchCatalog({
      TEAM_SWITCH_URL: "http://switch.test:17621",
      TEAM_SWITCH_TOKEN_FILE: path,
    }),
  );
});

test("worker authentication accepts only unambiguous user capabilities; inference caps are checked before forwarding", () => {
  const worker = "w".repeat(43);
  assert.equal(
    workerCapability({ authorization: `Bearer ${worker}` }, false),
    worker,
  );
  assert.equal(workerCapability({ "x-api-key": worker }, false), undefined);
  assert.equal(workerCapability({ "x-api-key": worker }, true), worker);
  assert.equal(
    workerCapability(
      { authorization: `Bearer ${worker}`, "x-api-key": worker },
      true,
    ),
    undefined,
  );
  assert.equal(
    workerCapability({ authorization: "Bearer bad" }, true),
    undefined,
  );
  const route = {
    protocol: "openai-chat",
    model: { maxTokens: 10 },
    switchModelId: "pinned-v1",
  };
  assert.throws(
    () => prepareSwitchInference(route, { max_tokens: 11 }),
    /output-limit/,
  );
  assert.throws(
    () => prepareSwitchInference(route, { max_output_tokens: -1 }),
    /output-limit/,
  );
  assert.deepEqual(
    prepareSwitchInference(route, { max_completion_tokens: 8 }),
    { model: "pinned-v1", max_tokens: 8 },
  );
});

test("editing a migrated connection revokes old session aliases and publishes only the new canonical revision", async () => {
  const endpoint = "http://switch.test:17621";
  let revision = 1;
  const catalog = new SwitchCatalog({
    endpoint,
    token,
    aliases,
    ttlMs: 0,
    fetcher: async () =>
      Response.json(manifest([model(endpoint, "openai-chat", revision)])),
  });
  try {
    const first = await catalog.route(
      "legacy-provider",
      "original-model",
      "openai-chat",
    );
    assert.ok(first.url.includes("/1/v1/"));
    revision = 2;
    assert.equal(
      await catalog.route("legacy-provider", "original-model", "openai-chat"),
      undefined,
    );
    const [service] = await catalog.list();
    assert.equal(service.id, `switch-${connection}`);
    assert.equal(service.models[0].id, `hq-${connection}-v2`);
    const current = await catalog.route(
      service.id,
      service.models[0].id,
      "openai-chat",
    );
    assert.ok(current.url.includes("/2/v1/"));
    assert.ok(
      first.url.includes("/1/v1/"),
      "an admitted request retains its immutable old URL",
    );
  } finally {
    catalog.close();
  }
});
