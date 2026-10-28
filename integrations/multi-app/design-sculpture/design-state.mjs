// Server-only stdin/stdout helper. Private payloads stay inside the host process.
import fs from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const req = createRequire('/opt/dsh/node_modules/@deepseek-ai/dsh/package.json');
let parse, stringify;
try { const yaml = req('yaml'); parse = yaml.parse; stringify = yaml.stringify; }
catch { const yaml = req('js-yaml'); parse = yaml.load; stringify = yaml.dump; }
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const hash = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');
const data = '/app/.od';
const dsh = data + '/sandbox/agent-home/.dsh';
const files = { settings: dsh + '/settings.yaml', credentials: dsh + '/.credentials.yaml', media: data + '/media-config.json', app: data + '/app-config.json' };
const directory = input.backupDirectory;
assert(directory.startsWith(data + '/deployment-backups/switch-text-') && !directory.includes('..'));
const read = (name) => fs.readFileSync(files[name]);
const atomic = (target, bytes) => {
  const temporary = target + '.switch-text-part-' + crypto.randomUUID();
  const descriptor = fs.openSync(temporary, 'wx', 0o600);
  try {
    try { fs.writeFileSync(descriptor, bytes); fs.fsyncSync(descriptor); }
    finally { fs.closeSync(descriptor); }
    fs.renameSync(temporary, target);
    const parent = fs.openSync(path.dirname(target), 'r');
    try { fs.fsyncSync(parent); } finally { fs.closeSync(parent); }
  } finally {
    fs.rmSync(temporary, { force: true });
  }
};
const Database = createRequire('/app/apps/daemon/package.json')('better-sqlite3');
const database = new Database(data + '/app.sqlite', { readonly: true, fileMustExist: true });
const markers = () => ({
  projectIds: hash(JSON.stringify(database.prepare('SELECT id FROM projects ORDER BY id').all())),
  conversationIds: hash(JSON.stringify(database.prepare('SELECT id FROM conversations ORDER BY id').all())),
  sessionBindings: hash(JSON.stringify(database.prepare('SELECT conversation_id,agent_id,session_id,model,cwd FROM agent_sessions ORDER BY conversation_id,agent_id').all())),
  activeTextRows: database.prepare("SELECT COUNT(*) AS count FROM messages WHERE run_status IN ('running','pending','queued','starting')").get().count,
});
try {
  if (input.action === 'prepare') {
    assert(!fs.existsSync(directory));
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    const before = Object.fromEntries(Object.keys(files).map((name) => [name, read(name)]));
    const currentMarkers = markers();
    assert.equal(currentMarkers.activeTextRows, 0);
    for (const [name, bytes] of Object.entries(before)) fs.writeFileSync(path.join(directory, name + '.before'), bytes, { flag: 'wx', mode: 0o600 });
    const sqliteBackup = path.join(directory, 'app-before.sqlite');
    await database.backup(sqliteBackup);
    fs.chmodSync(sqliteBackup, 0o600);
    const settings = parse(before.settings.toString('utf8'));
    const credentials = parse(before.credentials.toString('utf8'));
    const oldSettings = structuredClone(settings), oldCredentials = structuredClone(credentials);
    const provider = settings['llm-pi-ai'].providers.dashscope;
    assert.deepEqual(provider.models.map((item) => item.id).sort(), ['qwen-plus', 'qwen3.8-flash']);
    provider.baseURL = 'http://192.168.2.11:28001/model-api/hq/services/unconfigured/v1';
    provider.apiKeyEnv = 'HQ_SWITCH_CHAT_API_KEY';
    for (const model of provider.models) { assert(input.models[model.id]); model.baseURL = input.models[model.id]; }
    credentials.refs ??= {};
    assert(!credentials.refs.HQ_SWITCH_CHAT_API_KEY || credentials.refs.HQ_SWITCH_CHAT_API_KEY === input.inferenceToken);
    credentials.refs.HQ_SWITCH_CHAT_API_KEY = input.inferenceToken;
    for (const [key, value] of Object.entries(oldCredentials.refs ?? {})) assert.equal(credentials.refs[key], value);
    const next = { settings: Buffer.from(stringify(settings)), credentials: Buffer.from(stringify(credentials)) };
    assert.deepEqual(parse(next.settings.toString('utf8')), settings);
    assert.deepEqual(parse(next.credentials.toString('utf8')), credentials);
    for (const [name, bytes] of Object.entries(next)) fs.writeFileSync(path.join(directory, name + '.next'), bytes, { flag: 'wx', mode: 0o600 });
    const metadata = { beforeHashes: Object.fromEntries(Object.entries(before).map(([name, bytes]) => [name, hash(bytes)])), nextHashes: Object.fromEntries(Object.entries(next).map(([name, bytes]) => [name, hash(bytes)])), markers: currentMarkers, oldCredentialRefsPreserved: true };
    for (const name of Object.keys(files)) assert.equal(hash(read(name)), metadata.beforeHashes[name]);
    fs.writeFileSync(path.join(directory, 'metadata.json'), JSON.stringify(metadata), { flag: 'wx', mode: 0o600 });
    console.log(JSON.stringify(metadata));
  } else {
    const metadata = JSON.parse(fs.readFileSync(path.join(directory, 'metadata.json'), 'utf8'));
    assert.equal(hash(read('media')), metadata.beforeHashes.media);
    assert.equal(hash(read('app')), metadata.beforeHashes.app);
    if (input.action === 'apply') {
      for (const name of ['settings', 'credentials']) assert.equal(hash(read(name)), metadata.beforeHashes[name]);
      assert.equal(markers().activeTextRows, 0);
      atomic(files.credentials, fs.readFileSync(path.join(directory, 'credentials.next')));
      atomic(files.settings, fs.readFileSync(path.join(directory, 'settings.next')));
    } else if (input.action === 'rollback') {
      for (const name of ['settings', 'credentials']) assert([metadata.beforeHashes[name], metadata.nextHashes[name]].includes(hash(read(name))));
      atomic(files.credentials, fs.readFileSync(path.join(directory, 'credentials.before')));
      atomic(files.settings, fs.readFileSync(path.join(directory, 'settings.before')));
    } else assert.equal(input.action, 'verify');
    const expected = input.action === 'rollback' ? metadata.beforeHashes : metadata.nextHashes;
    for (const name of ['settings', 'credentials']) assert.equal(hash(read(name)), expected[name]);
    assert.deepEqual(markers(), metadata.markers);
    console.log(JSON.stringify({ passed: true, mediaUnchanged: true, appPreferencesUnchanged: true, oldCredentialRefsPreserved: true, sessionBindingsUnchanged: true }));
  }
} finally { database.close(); }
