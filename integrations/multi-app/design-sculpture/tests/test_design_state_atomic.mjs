import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';

// Exercise the actual server helper without loading production paths or SQLite.
const source = fs.readFileSync(new URL('../design-state.mjs', import.meta.url), 'utf8');
const begin = source.indexOf('const atomic = ');
const end = source.indexOf('\nconst Database =', begin);
assert(begin >= 0 && end > begin);
const declaration = source.slice(begin, end) + '\natomic;';

test('a previous interrupted temporary cannot block apply or rollback', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'hq-design-atomic-'));
  try {
    const target = path.join(directory, 'settings.yaml');
    fs.writeFileSync(target, 'before');
    fs.writeFileSync(target + '.switch-text-part', 'old interrupted write');
    const atomic = vm.runInNewContext(declaration, { fs, path, crypto });
    atomic(target, Buffer.from('next'));
    atomic(target, Buffer.from('before'));
    assert.equal(fs.readFileSync(target, 'utf8'), 'before');
    assert.equal(fs.readFileSync(target + '.switch-text-part', 'utf8'), 'old interrupted write');
    assert.deepEqual(fs.readdirSync(directory).sort(), ['settings.yaml', 'settings.yaml.switch-text-part']);
    assert.equal(fs.statSync(target).mode & 0o777, 0o600);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

for (const operation of ['writeFileSync', 'renameSync']) {
  test(`a ${operation} failure preserves the active file and cleans its own temporary`, () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'hq-design-atomic-'));
    try {
      const target = path.join(directory, 'settings.yaml');
      fs.writeFileSync(target, 'before');
      const failingFs = { ...fs, [operation]() { throw new Error('simulated filesystem failure'); } };
      const atomic = vm.runInNewContext(declaration, { fs: failingFs, path, crypto });
      assert.throws(() => atomic(target, Buffer.from('next')), /simulated filesystem failure/);
      assert.equal(fs.readFileSync(target, 'utf8'), 'before');
      assert.deepEqual(fs.readdirSync(directory), ['settings.yaml']);
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}
