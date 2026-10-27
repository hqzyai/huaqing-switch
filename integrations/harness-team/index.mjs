/** Server-only Switch credentials and credential-free Team/Pi catalog projection. */
import { readFile, stat } from "node:fs/promises";

const PROTOCOLS = new Set(["openai-chat", "openai-responses", "anthropic"]);
const SERVICE_ID = /^[a-z][a-z0-9-]{0,47}$/;
const MODEL_ID = /^[A-Za-z0-9_.:/-]{1,200}$/;
const CONNECTION_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const COMPAT = new Set(["deepseek", "qwen", "zai"]);
const text = (value, min, max) =>
  typeof value === "string" &&
  value.length >= min &&
  value.length <= max &&
  !/[\x00-\x1f\x7f]/.test(value);
const integer = (value, min, max) =>
  Number.isSafeInteger(value) && value >= min && value <= max;
const plain = (value) =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const nameOf = (value) => Array.from(value).slice(0, 100).join("");
const failed = () =>
  new Error("huaqing-switch: managed model directory unavailable");

/** Validate a managed catalog without trusting route URLs supplied by a worker. */
export function parseSwitchService(value) {
  if (
    !plain(value) ||
    value.provider !== "huaqing-switch" ||
    typeof value.id !== "string" ||
    !SERVICE_ID.test(value.id) ||
    !text(value.name, 1, 100) ||
    typeof value.enabled !== "boolean" ||
    !PROTOCOLS.has(value.protocol) ||
    value.baseURL !== "managed-by-huaqing-switch" ||
    !Array.isArray(value.models) ||
    value.models.length < 1 ||
    value.models.length > 100 ||
    (value.thinkingFormat !== undefined && !COMPAT.has(value.thinkingFormat))
  )
    throw failed();
  const ids = new Set();
  const models = value.models.map((model) => {
    if (
      !plain(model) ||
      typeof model.id !== "string" ||
      !MODEL_ID.test(model.id) ||
      ids.has(model.id) ||
      !text(model.name, 1, 100) ||
      !integer(model.contextWindow, 1024, 2_000_000) ||
      !integer(model.maxTokens, 1, model.contextWindow) ||
      typeof model.vision !== "boolean"
    )
      throw failed();
    ids.add(model.id);
    return {
      id: model.id,
      name: model.name,
      contextWindow: model.contextWindow,
      maxTokens: model.maxTokens,
      vision: model.vision,
    };
  });
  return {
    id: value.id,
    provider: "huaqing-switch",
    name: value.name,
    enabled: value.enabled,
    protocol: value.protocol,
    baseURL: "managed-by-huaqing-switch",
    models,
    ...(value.thinkingFormat === undefined
      ? {}
      : { thinkingFormat: value.thinkingFormat }),
  };
}

/** Use the existing Pi adapters; each worker retains only its own Team capability. */
export function switchProviderProfile(service, gateway) {
  service = parseSwitchService(service);
  const api = {
    "openai-chat": "openai-completions",
    "openai-responses": "openai-responses",
    anthropic: "anthropic-messages",
  }[service.protocol];
  return {
    displayName: service.name,
    api,
    apiKeyEnv: "DSH_TEAM_TOKEN",
    baseURL: new URL(
      `/internal/models/${service.id}${service.protocol === "anthropic" ? "" : "/v1"}`,
      gateway,
    ).href,
    models: service.models.map((model) => ({
      id: model.id,
      name: model.name,
      contextWindow: model.contextWindow,
      maxTokens: model.maxTokens,
      input: model.vision ? ["text", "image"] : ["text"],
    })),
    ...(service.protocol === "openai-chat"
      ? {
          compat: {
            supportsStore: false,
            supportsDeveloperRole: false,
            maxTokensField: "max_tokens",
            supportsUsageInStreaming: true,
            ...(service.thinkingFormat === undefined
              ? {}
              : { thinkingFormat: service.thinkingFormat }),
          },
        }
      : {}),
  };
}

function originOf(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw failed();
  }
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/"
  )
    throw failed();
  return url.origin;
}

function parseAliases(value) {
  if (
    !plain(value) ||
    value.version !== 1 ||
    !Array.isArray(value.aliases) ||
    value.aliases.length > 10000
  )
    throw failed();
  const keys = new Set();
  return value.aliases.map((alias) => {
    if (
      !plain(alias) ||
      typeof alias.serviceId !== "string" ||
      !SERVICE_ID.test(alias.serviceId) ||
      typeof alias.modelId !== "string" ||
      !MODEL_ID.test(alias.modelId) ||
      !CONNECTION_ID.test(alias.connectionId) ||
      !integer(alias.revision, 1, Number.MAX_SAFE_INTEGER) ||
      !text(alias.serviceName, 1, 100) ||
      !text(alias.modelName, 1, 100) ||
      typeof alias.enabled !== "boolean" ||
      (alias.thinkingFormat !== undefined && !COMPAT.has(alias.thinkingFormat))
    )
      throw failed();
    const key = `${alias.serviceId}/${alias.modelId}`;
    if (keys.has(key)) throw failed();
    keys.add(key);
    return { ...alias };
  });
}

function parseManifest(body, endpoint) {
  if (
    !plain(body) ||
    body.version !== 1 ||
    body.product !== "huaqing-switch" ||
    !Array.isArray(body.models) ||
    body.models.length > 100
  )
    throw failed();
  const ids = new Set();
  return body.models.map((model) => {
    const match =
      typeof model?.id === "string"
        ? /^hq-([0-9a-f-]{36})-v([1-9][0-9]{0,15})$/.exec(model.id)
        : null;
    if (
      !match ||
      !CONNECTION_ID.test(match[1]) ||
      !integer(Number(match[2]), 1, Number.MAX_SAFE_INTEGER) ||
      ids.has(match[1]) ||
      !text(model.name, 1, 400) ||
      !PROTOCOLS.has(model.protocol) ||
      !integer(model.contextWindow, 1024, 2_000_000) ||
      !integer(model.maxOutputTokens, 1, model.contextWindow) ||
      typeof model.supportsImages !== "boolean"
    )
      throw failed();
    const root = `${endpoint}/hq/connections/${match[1]}/${match[2]}`;
    const expected = model.protocol === "anthropic" ? root : `${root}/v1`;
    if (model.baseUrl !== expected) throw failed();
    ids.add(match[1]);
    return {
      connectionId: match[1],
      revision: Number(match[2]),
      id: model.id,
      name: model.name,
      protocol: model.protocol,
      url: `${root}/v1/${{ "openai-chat": "chat/completions", "openai-responses": "responses", anthropic: "messages" }[model.protocol]}`,
      contextWindow: model.contextWindow,
      maxTokens: model.maxOutputTokens,
      vision: model.supportsImages,
    };
  });
}

function snapshot(models, aliases) {
  const services = new Map(),
    routes = new Map(),
    aliased = new Set(
      aliases
        .filter((alias) => !alias.enabled)
        .map((alias) => alias.connectionId),
    );
  const byId = new Map(models.map((model) => [model.connectionId, model]));
  const add = (id, name, modelId, modelName, model, thinkingFormat) => {
    let service = services.get(id);
    if (
      service &&
      (service.protocol !== model.protocol ||
        service.thinkingFormat !== thinkingFormat)
    )
      throw failed();
    if (!service) {
      service = {
        id,
        provider: "huaqing-switch",
        name: nameOf(name),
        enabled: true,
        protocol: model.protocol,
        baseURL: "managed-by-huaqing-switch",
        models: [],
        ...(thinkingFormat === undefined ? {} : { thinkingFormat }),
      };
      services.set(id, service);
    }
    const entry = {
      id: modelId,
      name: nameOf(modelName),
      contextWindow: model.contextWindow,
      maxTokens: model.maxTokens,
      vision: model.vision,
    };
    service.models.push(entry);
    routes.set(`${id}/${modelId}`, {
      service,
      model: entry,
      protocol: model.protocol,
      url: model.url,
      switchModelId: model.id,
    });
  };
  for (const alias of aliases) {
    const model = byId.get(alias.connectionId);
    // Removal in Switch revokes both old session aliases and current discovery.
    if (alias.enabled && model && alias.revision === model.revision) {
      add(
        alias.serviceId,
        alias.serviceName,
        alias.modelId,
        alias.modelName,
        model,
        alias.thinkingFormat,
      );
      aliased.add(model.connectionId);
    }
  }
  for (const model of models)
    if (!aliased.has(model.connectionId)) {
      add(
        `switch-${model.connectionId}`,
        model.name,
        model.id,
        model.name,
        model,
      );
    }
  return { services: [...services.values()].map(parseSwitchService), routes };
}

async function boundedJson(response, limit) {
  if (!response.ok || !response.body) throw failed();
  const reader = response.body.getReader(),
    chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > limit) {
        await reader.cancel();
        throw failed();
      }
      chunks.push(Buffer.from(value));
    }
    return JSON.parse(Buffer.concat(chunks, size).toString("utf8"));
  } catch {
    throw failed();
  } finally {
    reader.releaseLock();
  }
}

/** A bounded atomic catalog cache. Routes pin one verified revision before inference. */
export class SwitchCatalog {
  #endpoint;
  #token;
  #aliases;
  #timeoutMs;
  #ttlMs;
  #maxBytes;
  #current;
  #expires = 0;
  #pending;
  #fetcher;
  #lifecycle = new AbortController();
  constructor({
    endpoint,
    token,
    aliases = { version: 1, aliases: [] },
    timeoutMs = 10000,
    ttlMs = 1000,
    maxBytes = 2 * 1024 * 1024,
    fetcher = fetch,
  }) {
    this.#endpoint = originOf(endpoint);
    if (
      !text(token, 32, 8192) ||
      !integer(timeoutMs, 1000, 120000) ||
      !integer(ttlMs, 0, 15000) ||
      !integer(maxBytes, 1024, 16 * 1024 * 1024)
    )
      throw failed();
    this.#token = token;
    this.#aliases = parseAliases(aliases);
    this.#timeoutMs = timeoutMs;
    this.#ttlMs = ttlMs;
    this.#maxBytes = maxBytes;
    this.#fetcher = fetcher;
  }
  close() {
    this.#lifecycle.abort();
    this.#current = undefined;
  }
  async #load() {
    if (this.#lifecycle.signal.aborted) throw failed();
    if (this.#current && performance.now() < this.#expires)
      return this.#current;
    if (this.#pending) return this.#pending;
    const operation = (async () => {
      const response = await this.#fetcher(`${this.#endpoint}/hq/manifest`, {
        headers: { authorization: `Bearer ${this.#token}` },
        redirect: "error",
        signal: AbortSignal.any([
          this.#lifecycle.signal,
          AbortSignal.timeout(this.#timeoutMs),
        ]),
      });
      const next = snapshot(
        parseManifest(
          await boundedJson(response, this.#maxBytes),
          this.#endpoint,
        ),
        this.#aliases,
      );
      if (this.#lifecycle.signal.aborted) throw failed();
      this.#current = next;
      this.#expires = performance.now() + this.#ttlMs;
      return next;
    })();
    this.#pending = operation;
    try {
      return await operation;
    } catch {
      throw failed();
    } finally {
      this.#pending = undefined;
    }
  }
  async list() {
    return structuredClone((await this.#load()).services);
  }
  async route(serviceId, modelId, protocol) {
    const route = (await this.#load()).routes.get(`${serviceId}/${modelId}`);
    if (!route || route.protocol !== protocol) return undefined;
    return { ...structuredClone(route), apiKey: this.#token };
  }
}

/** Load explicit deployment settings; absent settings retain the original cloud catalog. */
export async function loadSwitchCatalog(environment = process.env) {
  if (!environment.TEAM_SWITCH_URL) {
    if (
      environment.TEAM_SWITCH_TOKEN_FILE ||
      environment.TEAM_SWITCH_ALIASES_FILE ||
      environment.TEAM_SWITCH_MANAGEMENT_URL
    )
      throw failed();
    return undefined;
  }
  const path = environment.TEAM_SWITCH_TOKEN_FILE;
  if (!path) throw failed();
  const info = await stat(path);
  if (!info.isFile() || (info.mode & 0o077) !== 0 || info.size > 8192)
    throw failed();
  const token = (await readFile(path, "utf8")).trim();
  let aliases = { version: 1, aliases: [] };
  if (environment.TEAM_SWITCH_ALIASES_FILE) {
    const info = await stat(environment.TEAM_SWITCH_ALIASES_FILE);
    if (!info.isFile() || info.size > 4 * 1024 * 1024) throw failed();
    aliases = JSON.parse(
      await readFile(environment.TEAM_SWITCH_ALIASES_FILE, "utf8"),
    );
  }
  return new SwitchCatalog({
    endpoint: environment.TEAM_SWITCH_URL,
    token,
    aliases,
    timeoutMs: Number(environment.TEAM_SWITCH_TIMEOUT_MS ?? 10000),
    ttlMs: Number(environment.TEAM_SWITCH_CACHE_MS ?? 1000),
  });
}

/** Public management URL is informational; it never carries a credential or redirect target. */
export function switchManagementUrl(environment = process.env) {
  if (!environment.TEAM_SWITCH_MANAGEMENT_URL) throw failed();
  const origin = originOf(environment.TEAM_SWITCH_MANAGEMENT_URL);
  if (!origin.startsWith("https://")) throw failed();
  return origin;
}

/** Enforce the advertised per-request cap using the native protocol's output field. */
export function prepareSwitchInference(route, body) {
  if (!plain(body)) throw failed();
  for (const key of [
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
  ]) {
    if (
      body[key] !== undefined &&
      !integer(body[key], 1, route.model.maxTokens)
    )
      throw new Error("model-output-limit");
  }
  body.model = route.switchModelId;
  const field =
    route.protocol === "openai-responses" ? "max_output_tokens" : "max_tokens";
  body[field] =
    body[field] ?? body.max_completion_tokens ?? route.model.maxTokens;
  for (const key of [
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
  ])
    if (key !== field) delete body[key];
  return body;
}

/** Forward only protocol version hints; credentials always come from the server's route. */
export function switchInferenceHeaders(route, incoming = {}) {
  const headers = {
    "content-type": "application/json",
    authorization: `Bearer ${route.apiKey}`,
  };
  for (const key of ["anthropic-version", "anthropic-beta", "openai-beta"]) {
    const value = incoming[key];
    if (text(value, 1, 4096)) headers[key] = value;
  }
  return headers;
}

/** Accept the existing worker bearer or, only for managed native APIs, its x-api-key form. */
export function workerCapability(headers, allowApiKey) {
  const authorization = headers.authorization,
    apiKey = headers["x-api-key"];
  if (authorization !== undefined && apiKey !== undefined) return undefined;
  const value =
    typeof authorization === "string" && authorization.startsWith("Bearer ")
      ? authorization.slice(7)
      : authorization === undefined && allowApiKey && typeof apiKey === "string"
        ? apiKey
        : undefined;
  return typeof value === "string" && /^[A-Za-z0-9_-]{43}$/.test(value)
    ? value
    : undefined;
}
