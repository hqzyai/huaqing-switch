/** Reproduce only the reviewed Switch extension over immutable deployed Team inputs. */
import { readFile, writeFile, mkdir, cp } from "node:fs/promises";
import { resolve, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const [baseline, destination] = process.argv.slice(2);
if (!baseline || !destination || process.argv.length !== 4)
  throw new Error(
    "Usage: node patch-baseline.mjs BASELINE_DIRECTORY OUTPUT_DIRECTORY",
  );
const pinned = JSON.parse(
  await readFile(join(here, "baseline-sha256.json"), "utf8"),
);
const input = {};
for (const [name, hash] of Object.entries(pinned)) {
  const bytes = await readFile(join(baseline, name));
  if (createHash("sha256").update(bytes).digest("hex") !== hash)
    throw new Error(`Unsupported Team baseline: ${name}`);
  input[name] = bytes.toString("utf8");
}
function once(text, from, to) {
  if (!text.includes(from) || text.indexOf(from) !== text.lastIndexOf(from))
    throw new Error("Team patch anchor mismatch");
  return text.replace(from, to);
}
const importNames =
  "loadSwitchCatalog, switchManagementUrl, prepareSwitchInference, switchInferenceHeaders, workerCapability";
function server(text, js) {
  text =
    `import { ${importNames} } from '@hqzyai/huaqing-switch-team'${js ? ";" : ""}\n` +
    text;
  const q = js ? '"' : "'",
    ending = js ? ";" : "";
  const modelLine = js
    ? '\tconst models = new TeamModels(join(dataDir, "models.sqlite"), encryptionKey);'
    : "  const models = new TeamModels(join(dataDir, 'models.sqlite'), encryptionKey)";
  const indent = js ? "\t" : "  ";
  text = once(
    text,
    modelLine,
    modelLine +
      `\n${indent}const switchCatalog = await loadSwitchCatalog()${ending}\n${indent}const switchUI = switchCatalog ? switchManagementUrl() : undefined${ending}\n${indent}if (switchCatalog) { releases.push(() => switchCatalog.close()); await switchCatalog.list() }`,
  );
  const tokenLine = js
    ? 'const token = req.headers.authorization?.replace(/^Bearer /, "");'
    : "const token = req.headers.authorization?.replace(/^Bearer /, '')";
  text = once(
    text,
    tokenLine,
    `const token = workerCapability(req.headers, switchCatalog !== undefined)${ending}`,
  );
  // Both browser and worker catalog paths share one current, verified directory.
  const listTrue = `services: models.list(true)`;
  if (text.split(listTrue).length !== 3)
    throw new Error("Team catalog anchor count changed");
  text = text.replaceAll(
    listTrue,
    "services: switchCatalog ? await switchCatalog.list() : models.list(true)",
  );
  text = once(
    text,
    "services: models.list(false)",
    "services: switchCatalog ? await switchCatalog.list() : models.list(false), ...(switchCatalog ? {managedBySwitch: {url: switchUI}} : {})",
  );
  const postGuard = js
    ? '\t\tif (req.method !== "POST") throw new HttpFailure(405, "invalid");'
    : "    if (req.method !== 'POST') throw new HttpFailure(405, 'invalid')";
  text = once(
    text,
    postGuard,
    `${js ? "\t\t" : "    "}if (switchCatalog && (path === ${q}/team/api/services${q} || path === ${q}/team/api/model-discovery${q})) throw new HttpFailure(409, ${q}models-managed-by-switch${q})${ending}\n` +
      postGuard,
  );
  const oldRegex =
    "/^\\/internal\\/models\\/([a-z][a-z0-9-]{0,47})\\/v1\\/chat\\/completions$/";
  const newRegex =
    "/^\\/internal\\/models\\/([a-z][a-z0-9-]{0,47})\\/v1\\/(chat\\/completions|responses|messages)$/";
  text = once(text, oldRegex, newRegex);
  const routeLine = js
    ? 'const route = models.route(match[1], textField(body, "model"));'
    : "const route = models.route(match[1], textField(body, 'model'))";
  text = once(
    text,
    routeLine,
    `const protocol = match[2] === ${q}responses${q} ? ${q}openai-responses${q} : match[2] === ${q}messages${q} ? ${q}anthropic${q} : ${q}openai-chat${q}${ending}\n${js ? "\t\t\t" : "      "}const switchRoute = switchCatalog ? await switchCatalog.route(match[1], textField(body, ${q}model${q}), protocol) : undefined${ending}\n${js ? "\t\t\t" : "      "}const route = switchRoute ?? (!switchCatalog && protocol === ${q}openai-chat${q} ? models.route(match[1], textField(body, ${q}model${q})) : undefined)${ending}`,
  );
  const from = js
    ? "\t\t\tbody.max_tokens = body.max_tokens ?? body.max_completion_tokens ?? model.maxTokens;\n\t\t\tdelete body.max_completion_tokens;"
    : "      body.max_tokens = body.max_tokens ?? body.max_completion_tokens ?? model.maxTokens\n      delete body.max_completion_tokens";
  const blockIndent = js ? "\t\t\t" : "      ";
  text = once(
    text,
    from,
    `${blockIndent}if (switchRoute) {\n${blockIndent}  try { prepareSwitchInference(switchRoute, body) } catch { throw new HttpFailure(400, ${q}model-output-limit${q}) }\n${blockIndent}} else {\n${from}\n${blockIndent}}`,
  );
  text = once(
    text,
    "fetch(`${route.service.baseURL}/chat/completions`, {",
    "fetch(switchRoute ? switchRoute.url : `${route.service.baseURL}/chat/completions`, {",
  );
  const oldHeaders = js
    ? '\t\t\t\theaders: {\n\t\t\t\t\t"content-type": "application/json",\n\t\t\t\t\tauthorization: `Bearer ${route.apiKey}`\n\t\t\t\t},'
    : "headers: { 'content-type': 'application/json', authorization: `Bearer ${route.apiKey}` },";
  const newHeaders = js
    ? '\t\t\t\theaders: switchRoute ? switchInferenceHeaders(switchRoute, req.headers) : {"content-type": "application/json", authorization: `Bearer ${route.apiKey}`},'
    : "headers: switchRoute ? switchInferenceHeaders(switchRoute, req.headers) : { 'content-type': 'application/json', authorization: `Bearer ${route.apiKey}` },";
  text = once(text, oldHeaders, newHeaders);
  return text;
}
function worker(text, js) {
  text =
    `import { parseSwitchService, switchProviderProfile } from '@hqzyai/huaqing-switch-team'${js ? ";" : ""}\n` +
    text;
  const old = js
    ? "const service = parseModelService(raw);"
    : "const service = parseModelService(raw)";
  const next = js
    ? 'const managed = typeof raw === "object" && raw !== null && raw.provider === "huaqing-switch" ? parseSwitchService(raw) : undefined;\n\t\t\tif (managed) { if (managed.enabled) providers[`team-${managed.id}`] = switchProviderProfile(managed, gateway); continue; }\n\t\t\t'
    : "const managed = typeof raw === 'object' && raw !== null && 'provider' in raw && raw.provider === 'huaqing-switch' ? parseSwitchService(raw) : undefined\n      if (managed) { if (managed.enabled) providers[`team-${managed.id}`] = switchProviderProfile(managed, gateway); continue }\n      ";
  return once(text, old, next + old);
}
function portal(text, js) {
  const prefix = js ? "\t" : "  ";
  const key = js ? '"modelSetupHint": "' : "'modelSetupHint': '";
  text = once(
    text,
    `${key}连接模型平台并读取目录，再选择提供给团队的模型${js ? '",' : "',"}`,
    `${key}连接模型平台并读取目录，再选择提供给团队的模型${js ? '",' : "',"}\n${prefix}${js ? '"managedSwitch": "模型由 Huaqing Switch 统一管理", "openSwitch": "打开 Switch 管理", "models-managed-by-switch": "请在 Huaqing Switch 中修改模型配置",' : "'managedSwitch': '模型由 Huaqing Switch 统一管理', 'openSwitch': '打开 Switch 管理', 'models-managed-by-switch': '请在 Huaqing Switch 中修改模型配置',"}`,
  );
  text = once(
    text,
    `${key}Connect a provider, read its catalog, then select models for your team.${js ? '",' : "',"}`,
    `${key}Connect a provider, read its catalog, then select models for your team.${js ? '",' : "',"}\n${prefix}${js ? '"managedSwitch": "Models are managed by Huaqing Switch", "openSwitch": "Open Switch management", "models-managed-by-switch": "Change model configuration in Huaqing Switch",' : "'managedSwitch': 'Models are managed by Huaqing Switch', 'openSwitch': 'Open Switch management', 'models-managed-by-switch': 'Change model configuration in Huaqing Switch',"}`,
  );
  text = once(
    text,
    "  append(section,el('h2',t.models),el('p',t.modelSetupHint),button(t.create,()=>edit()),list,editor);",
    "  const createButton=button(t.create,()=>edit());\n  append(section,el('h2',t.models),el('p',t.modelSetupHint),createButton,list,editor);",
  );
  text = once(
    text,
    "    const {services}=await api('services');list.replaceChildren();",
    "    const {services,managedBySwitch}=await api('services');list.replaceChildren();createButton.hidden=!!managedBySwitch;\n    if(managedBySwitch){append(list,el('p',t.managedSwitch),el('a',t.openSwitch,{href:managedBySwitch.url,target:'_blank',rel:'noopener noreferrer'}));}",
  );
  text = once(
    text,
    "      append(row,el('span',service.name+' · '+service.models.map(x=>x.name).join(', ')+' · '+(service.enabled?t.enabledStatus:t.disabled)),button(t.edit,()=>edit(service)));",
    "      append(row,el('span',service.name+' · '+service.models.map(x=>x.name).join(', ')+' · '+(service.enabled?t.enabledStatus:t.disabled)));\n      if(!managedBySwitch)row.append(button(t.edit,()=>edit(service)));",
  );
  return text;
}
const outputs = {
  "artifacts/team-server-index.js": portal(
    server(input["team-server-index.js"], true),
    true,
  ),
  "artifacts/team-runtime-index.js": worker(
    input["team-runtime-index.js"],
    true,
  ),
  "sources/packages/host/team-server/src/index.ts": server(
    input["team-server-current-index.ts"],
    false,
  ),
  "sources/packages/host/team-server/src/portal.ts": portal(
    input["team-server-current-portal.ts"],
    false,
  ),
  "sources/packages/host/team-runtime/src/index.ts": worker(
    input["team-runtime-index.ts"],
    false,
  ),
};
for (const [name, contents] of Object.entries(outputs)) {
  const path = join(resolve(destination), name);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, contents);
}
await cp(here, join(resolve(destination), "addon"), {
  recursive: true,
  filter: (path) => !path.includes("/tests"),
});
await writeFile(
  join(resolve(destination), "patch-manifest.json"),
  JSON.stringify(
    {
      version: 1,
      baseline: pinned,
      output: Object.fromEntries(
        Object.entries(outputs).map(([name, text]) => [
          name,
          createHash("sha256").update(text).digest("hex"),
        ]),
      ),
    },
    null,
    2,
  ) + "\n",
);
console.log("Verified Team baseline; generated source and runtime extensions.");
