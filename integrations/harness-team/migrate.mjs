/** Copy the existing encrypted Team catalog into an empty Switch, preserving session selectors. Run only on the deployment server. */
import {
  readFile,
  writeFile,
  rename,
  mkdtemp,
  rm,
  stat,
  chmod,
} from "node:fs/promises";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import { DatabaseSync, backup } from "node:sqlite";
import { TeamModels } from "@deepseek-ai/dsh-team-models";

const env = process.env;
const required = (name) => {
  const value = env[name];
  if (!value) throw new Error(`Missing deployment setting: ${name}`);
  return value;
};
const privateRead = async (path) => {
  const info = await stat(path);
  if (!info.isFile() || (info.mode & 0o077) !== 0 || info.size > 8192)
    throw new Error("Migration requires a private credential file");
  return (await readFile(path, "utf8")).trim();
};
const snapshotDirectory = required("TEAM_SWITCH_MIGRATION_BACKUP_DIR");
const aliasesPath = required("TEAM_SWITCH_ALIASES_FILE");
const existingDatabase = join(required("TEAM_DATA_DIR"), "models.sqlite");
const encryptionKey = await privateRead(required("TEAM_ENCRYPTION_KEY_FILE"));
const admin = await privateRead(required("TEAM_SWITCH_ADMIN_TOKEN_FILE"));
const endpoint = new URL(required("TEAM_SWITCH_ADMIN_URL"));
if (
  !["http:", "https:"].includes(endpoint.protocol) ||
  endpoint.username ||
  endpoint.password ||
  endpoint.search ||
  endpoint.hash ||
  endpoint.pathname !== "/"
)
  throw new Error("Invalid migration endpoint");
if (env.TEAM_SWITCH_MIGRATION_CONFIRM !== "copy-catalog-to-empty-switch")
  throw new Error("Migration requires explicit empty-Switch confirmation");
await chmod(snapshotDirectory, 0o700);
try {
  await stat(aliasesPath);
  throw new Error("Migration aliases already exist; preserve them and stop");
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
const temporary = await mkdtemp(join(tmpdir(), "hq-switch-catalog-migration-"));
let models, original;
let count = 0;
try {
  original = new DatabaseSync(existingDatabase, { readOnly: true });
  const snapshot = join(snapshotDirectory, "team-models-before-switch.sqlite");
  try {
    await stat(snapshot);
    throw new Error(
      "Migration backup already exists; use a new backup directory",
    );
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  await backup(original, snapshot);
  await chmod(snapshot, 0o600);
  const clone = join(temporary, "models.sqlite");
  await backup(original, clone);
  original.close();
  original = undefined;
  models = new TeamModels(clone, encryptionKey);
  const services = models.list(false);
  const totalModels = services.reduce(
    (count, service) => count + service.models.length,
    0,
  );
  if (
    totalModels > 100 ||
    services.some((service) =>
      service.models.some((model) => model.contextWindow > 2_000_000),
    )
  )
    throw new Error(
      "Existing catalog exceeds Switch capacity; no target writes admitted",
    );
  const command = async (name, body = {}) => {
    const response = await fetch(new URL(`/api/commands/${name}`, endpoint), {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${admin}`,
      },
      body: JSON.stringify(body),
      redirect: "error",
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw new Error("Switch catalog migration command failed");
    }
    return await response.json();
  };
  let current = await command("hq_get_connections");
  if (
    !Array.isArray(current.connections) ||
    current.connections.length !== 0 ||
    !Number.isSafeInteger(current.revision)
  )
    throw new Error("Migration target must be an empty Switch database");
  const aliases = [],
    enabledIds = [];
  for (const service of services)
    for (const model of service.models) {
      const route = models.route(service.id, model.id);
      // Disabled routes still need preserved private credentials, without enabling their Team aliases.
      const connection = route ?? {
        apiKey: models.resolveConnection({
          id: service.id,
          provider: service.provider,
          baseURL: service.baseURL,
        }).apiKey,
      };
      const previous = new Set(current.connections.map((item) => item.id));
      current = await command("hq_save_connection", {
        revision: current.revision,
        connection: {
          name: Array.from(`${service.name} · ${model.name}`)
            .slice(0, 80)
            .join(""),
          protocol: "openai-chat",
          baseUrl: service.baseURL,
          model: model.id,
          apiKey: connection.apiKey,
          contextWindow: model.contextWindow,
          maxOutputTokens: model.maxTokens,
          supportsImages: model.vision,
        },
      });
      const added = current.connections.filter(
        (item) => !previous.has(item.id),
      );
      if (added.length !== 1)
        throw new Error(
          "Migration target returned an unexpected connection set",
        );
      const id = added[0].id;
      aliases.push({
        serviceId: service.id,
        modelId: model.id,
        connectionId: id,
        revision: 1,
        serviceName: service.name,
        modelName: model.name,
        enabled: service.enabled,
        ...(service.provider === "deepseek"
          ? { thinkingFormat: "deepseek" }
          : service.provider === "qwen" || service.provider === "bailian"
            ? { thinkingFormat: "qwen" }
            : service.provider === "zhipu"
              ? { thinkingFormat: "zai" }
              : {}),
      });
      if (service.enabled) enabledIds.push(id);
      count++;
    }
  if (enabledIds.length)
    current = await command("hq_select_connection", {
      revision: current.revision,
      id: enabledIds[0],
    });
  if (current.connections.length !== aliases.length)
    throw new Error("Migration target changed concurrently");
  const path = aliasesPath + ".part";
  await writeFile(
    path,
    JSON.stringify({ version: 1, aliases }, null, 2) + "\n",
    { flag: "wx", mode: 0o600 },
  );
  await rename(path, aliasesPath);
  const result = {
    passed: true,
    sourceServiceCount: services.length,
    migratedConnectionCount: count,
    enabledAliasCount: enabledIds.length,
    originalCatalogUnmodified: true,
    credentialsPrinted: false,
    paidModelCall: false,
    oldSessionIdentifiersPreserved: true,
  };
  await writeFile(
    join(snapshotDirectory, "switch-migration-report.json"),
    JSON.stringify(result, null, 2) + "\n",
    { flag: "wx", mode: 0o600 },
  );
  console.log(JSON.stringify(result));
} catch {
  // Never print a provider URL, credential, request body, or cause from cryptography/networking.
  console.error(
    JSON.stringify({
      passed: false,
      migratedConnectionCount: count,
      message:
        "Migration stopped. Preserve backups and restore the isolated Switch target before retrying; Team production catalog was not changed.",
    }),
  );
  process.exitCode = 1;
} finally {
  original?.close();
  models?.close();
  await rm(temporary, { recursive: true, force: true });
}
