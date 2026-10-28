// Run with: node --experimental-vm-modules --experimental-import-meta-resolve verify-design.mjs
// The isolated VM exposes one private pure resolver only to this test. The
// production module remains byte-for-byte the installed two-seam overlay.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import vm from 'node:vm';
import { pathToFileURL } from 'node:url';

const modulePath = '/opt/dsh/node_modules/@deepseek-ai/dsh-llm-pi-ai/lib/index.js';
const source = await fs.readFile(modulePath, 'utf8');
assert(source.includes('const baseUrl = entry.baseURL ?? request.baseURL'));
assert(source.includes('const modelFields = {\n\tbaseURL: z.string(),'));
const tested = new vm.SourceTextModule(source + '\nexport { resolveProfiles as fixtureResolveProfiles };\n', {
  identifier: pathToFileURL(modulePath).href,
});
const dependencies = new Map();
await tested.link(async (specifier) => {
  if (dependencies.has(specifier)) return dependencies.get(specifier);
  const url = specifier.startsWith('node:') ? specifier : import.meta.resolve(specifier, pathToFileURL(modulePath).href);
  const loaded = await import(url);
  const exports = Object.keys(loaded);
  const linked = new vm.SyntheticModule(exports, function () {
    for (const name of exports) this.setExport(name, loaded[name]);
  });
  dependencies.set(specifier, linked);
  return linked;
});
await tested.evaluate();
const { Config, PiAiAdapter, fixtureResolveProfiles } = tested.namespace;
const token = 'a'.repeat(64);
const requests = [];
const server = http.createServer(async (request, response) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const body = JSON.parse(Buffer.concat(chunks));
  requests.push({ path: request.url, model: body.model, bearer: request.headers.authorization });
  if (request.url.includes('/unconfigured/')) {
    response.writeHead(404, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ error: { message: 'fixture unconfigured model route' } }));
    return;
  }
  response.writeHead(200, { 'content-type': 'text/event-stream' });
  const frame = (value) => response.write('data: ' + JSON.stringify(value) + '\n\n');
  const base = { id: 'fixture-chat', object: 'chat.completion.chunk', created: 1, model: body.model };
  frame({ ...base, choices: [{ index: 0, delta: { role: 'assistant', content: 'fixture:' + body.model }, finish_reason: null }] });
  frame({ ...base, choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 2, completion_tokens: 1, total_tokens: 3 } });
  response.end('data: [DONE]\n\n');
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const origin = 'http://127.0.0.1:' + server.address().port;
const configured = {
  providers: {
    dashscope: {
      displayName: 'Switch managed text models',
      api: 'openai-completions',
      apiKeyEnv: 'HQ_SWITCH_CHAT_API_KEY',
      baseURL: origin + '/model-api/hq/services/unconfigured/v1',
      models: [
        { id: 'qwen-plus', baseURL: origin + '/model-api/hq/services/qwen-plus/v1' },
        { id: 'qwen3.8-flash', baseURL: origin + '/model-api/hq/services/qwen-flash/v1' },
      ],
      compat: { supportsDeveloperRole: false, maxTokensField: 'max_completion_tokens' },
    },
  },
};
const checks = {};
try {
  const validated = Config(configured);
  checks.schemaKeepsPerModelUrls = validated.providers.dashscope.models.every((model) => typeof model.baseURL === 'string');
  const profiles = fixtureResolveProfiles(validated.providers);
  const adapter = new PiAiAdapter({ profiles: () => profiles, auth: {}, resolveApiKey: async () => token });
  const listed = await adapter.listModels('dashscope');
  assert.deepEqual(listed.map((model) => model.id), ['qwen-plus', 'qwen3.8-flash']);
  checks.providerAndModelIdsPreserved = true;
  for (const model of ['qwen-plus', 'qwen3.8-flash']) {
    const chunks = [];
    for await (const chunk of adapter.stream({ provider: 'dashscope', model, messages: [{ role: 'user', content: [{ type: 'text', text: 'isolated fixture' }] }], maxTokens: 32, signal: AbortSignal.timeout(8000) })) chunks.push(chunk);
    assert.equal(chunks.filter((chunk) => chunk.type === 'text-delta').map((chunk) => chunk.text).join(''), 'fixture:' + model);
    assert(chunks.some((chunk) => chunk.type === 'finish' && chunk.reason?.kind === 'stop'));
  }
  assert.deepEqual(requests.map(({ path, model, bearer }) => ({ path, model, bearer })), [
    { path: '/model-api/hq/services/qwen-plus/v1/chat/completions', model: 'qwen-plus', bearer: 'Bearer ' + token },
    { path: '/model-api/hq/services/qwen-flash/v1/chat/completions', model: 'qwen3.8-flash', bearer: 'Bearer ' + token },
  ]);
  checks.actualInstalledPiStreamsUseDistinctModelRoutes = true;
  checks.machineCredentialUsed = true;
  const missing = Config({ providers: { dashscope: { ...configured.providers.dashscope, models: [{ id: 'not-migrated' }] } } });
  const failProfiles = fixtureResolveProfiles(missing.providers);
  const blocked = new PiAiAdapter({ profiles: () => failProfiles, auth: {}, resolveApiKey: async () => token });
  const blockedChunks = [];
  for await (const chunk of blocked.stream({ provider: 'dashscope', model: 'not-migrated', messages: [{ role: 'user', content: [{ type: 'text', text: 'isolated failure fixture' }] }], maxTokens: 32, signal: AbortSignal.timeout(8000) })) blockedChunks.push(chunk);
  assert(requests.at(-1).path.includes('/unconfigured/'));
  assert(blockedChunks.some((chunk) => chunk.type === 'finish' && chunk.reason?.kind === 'error'));
  checks.missingModelUsesRefusedSwitchRoute = true;
  assert(Object.values(checks).every(Boolean));
  console.log(JSON.stringify({ passed: true, checks, paidModelCall: false, productionCredentialsRead: false }, null, 2));
} finally {
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
