import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';

// Run the complete helper against temporary files and an in-memory read-only
// SQLite seam. No installed packages, production paths or real data are loaded.
const source = fs.readFileSync(new URL('../design-state.mjs', import.meta.url), 'utf8');

async function fixture(run) {
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'hq-design-state-'));
  try {
    const dsh = path.join(data, 'sandbox/agent-home/.dsh');
    fs.mkdirSync(dsh, { recursive: true, mode: 0o700 });
    const files = {
      settings: path.join(dsh, 'settings.yaml'), credentials: path.join(dsh, '.credentials.yaml'),
      media: path.join(data, 'media-config.json'), app: path.join(data, 'app-config.json'),
    };
    const values = {
      settings: { 'llm-pi-ai': { providers: { dashscope: {
        api: 'openai-completions', apiKeyEnv: 'DASHSCOPE_API_KEY', baseURL: 'https://fixture.invalid/v1',
        models: [{ id: 'qwen-plus' }, { id: 'qwen3.8-flash' }],
      } } } },
      credentials: { refs: { DASHSCOPE_API_KEY: 'fake-provider-only' } },
      media: { fixtureMedia: 'original' }, app: { fixturePreference: 'original' },
    };
    for (const [name, value] of Object.entries(values)) fs.writeFileSync(files[name], JSON.stringify(value), { mode: 0o600 });
    const rows = {
      projects: [{ id: 'project-1' }], conversations: [{ id: 'conversation-1' }],
      sessions: [{ conversation_id: 'conversation-1', agent_id: 'fixture-agent', session_id: 'session-1', model: 'dashscope/qwen-plus', cwd: '/fixture' }],
      activeTextRows: 0,
    };
    class FixtureDatabase {
      constructor(databasePath, options) {
        assert.equal(databasePath, path.join(data, 'app.sqlite'));
        assert.equal(options.readonly, true);
        assert.equal(options.fileMustExist, true);
      }
      prepare(sql) {
        const key = sql.includes('FROM projects') ? 'projects'
          : sql.includes('FROM conversations') ? 'conversations'
            : sql.includes('FROM agent_sessions') ? 'sessions' : null;
        if (key) return { all: () => structuredClone(rows[key]) };
        assert(sql.includes('COUNT(*) AS count FROM messages'));
        return { get: () => ({ count: rows.activeTextRows }) };
      }
      async backup(target) { fs.writeFileSync(target, 'fixture-only backup'); }
      close() {}
    }
    const backupDirectory = path.join(data, 'deployment-backups/switch-text-fixture');
    const body = source.replace(/^import .*;\n/gm, '')
      .replace("const data = '/app/.od';", 'const data = ' + JSON.stringify(data) + ';');
    async function action(name) {
      const input = { action: name, backupDirectory, inferenceToken: 'a'.repeat(64), models: {
        'qwen-plus': 'http://127.0.0.1:28001/model-api/hq/services/plus/v1',
        'qwen3.8-flash': 'http://127.0.0.1:28001/model-api/hq/services/flash/v1',
      } };
      const output = [];
      const context = vm.createContext({
        fs: { ...fs, readFileSync(target, options) {
          return target === 0 ? JSON.stringify(input) : fs.readFileSync(target, options);
        } },
        crypto, path, assert, Buffer, structuredClone,
        console: { log: (value) => output.push(JSON.parse(value)) },
        createRequire: () => (name) => {
          if (name === 'yaml') return {
            parse: (text) => vm.runInContext('JSON.parse(' + JSON.stringify(text) + ')', context),
            stringify: JSON.stringify,
          };
          assert.equal(name, 'better-sqlite3');
          return FixtureDatabase;
        },
      });
      await vm.runInContext('(async () => {\n' + body + '\n})()', context);
      assert.equal(output.length, 1);
      return output[0];
    }
    const snapshot = () => Object.fromEntries(Object.entries(files).map(([name, target]) => [name, fs.readFileSync(target, 'utf8')]));
    const useCandidate = () => {
      for (const name of ['settings', 'credentials']) fs.copyFileSync(path.join(backupDirectory, name + '.next'), files[name]);
    };
    await action('prepare');
    await run({ action, backupDirectory, files, rows, snapshot, useCandidate });
  } finally {
    fs.rmSync(data, { recursive: true, force: true });
  }
}

test('unchanged prepared state supports preflight, apply, verify and rollback', async () => {
  await fixture(async ({ action, snapshot }) => {
    const before = snapshot();
    assert.equal((await action('preflight')).passed, true);
    assert.deepEqual(snapshot(), before);
    assert.equal((await action('apply')).passed, true);
    assert.notEqual(snapshot().settings, before.settings);
    assert.equal((await action('verify')).passed, true);
    const restored = await action('rollback');
    assert.equal(restored.passed, true);
    assert.equal(restored.stateReference, 'rollback-start');
    assert.deepEqual(snapshot(), before);
  });
});

test('a legitimate new session makes preflight and apply fail before either chat file is written', async () => {
  await fixture(async ({ action, rows, snapshot }) => {
    rows.sessions.push({ conversation_id: 'conversation-1', agent_id: 'another-agent', session_id: 'new-session', model: 'dashscope/qwen-plus', cwd: '/fixture' });
    const before = snapshot();
    const databaseBefore = structuredClone(rows);
    await assert.rejects(() => action('preflight'));
    assert.deepEqual(snapshot(), before);
    await assert.rejects(() => action('apply'));
    assert.deepEqual(snapshot(), before);
    assert.deepEqual(rows, databaseBefore);
  });
});

test('rollback restores only chat configuration and preserves new sessions, active rows and current nontext files', async () => {
  await fixture(async ({ action, files, rows, snapshot, useCandidate }) => {
    const original = snapshot();
    useCandidate();
    rows.projects.push({ id: 'new-project' });
    rows.conversations.push({ id: 'new-conversation' });
    rows.sessions.push({ conversation_id: 'new-conversation', agent_id: 'fixture-agent', session_id: 'new-session', model: 'dashscope/qwen-plus', cwd: '/fixture' });
    rows.activeTextRows = 1;
    fs.writeFileSync(files.media, '{"fixtureMedia":"legitimate newer configuration"}');
    fs.writeFileSync(files.app, '{"fixturePreference":"legitimate newer preference"}');
    const current = snapshot();
    const databaseBefore = structuredClone(rows);
    assert.equal((await action('rollback')).passed, true);
    assert.deepEqual(snapshot(), { ...current, settings: original.settings, credentials: original.credentials });
    assert.deepEqual(rows, databaseBefore);
  });
});

test('an unknown chat edit blocks rollback before any chat file is overwritten', async () => {
  await fixture(async ({ action, files, snapshot, useCandidate }) => {
    useCandidate();
    fs.writeFileSync(files.settings, 'unknown operator edit');
    const before = snapshot();
    await assert.rejects(() => action('rollback'));
    assert.deepEqual(snapshot(), before);
  });
});

test('normal verification still rejects session or nontext configuration drift', async () => {
  await fixture(async ({ action, rows, files }) => {
    await action('apply');
    rows.activeTextRows = 1;
    await assert.rejects(() => action('verify'));
    rows.activeTextRows = 0;
    fs.writeFileSync(files.app, '{"fixturePreference":"changed"}');
    await assert.rejects(() => action('verify'));
  });
});

test('corrupt backup bytes are rejected before rollback changes either active chat file', async () => {
  await fixture(async ({ action, backupDirectory, snapshot, useCandidate }) => {
    useCandidate();
    fs.writeFileSync(path.join(backupDirectory, 'settings.before'), 'corrupt backup');
    const before = snapshot();
    await assert.rejects(() => action('rollback'));
    assert.deepEqual(snapshot(), before);
  });
});
