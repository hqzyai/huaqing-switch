import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import {
  mkdtemp,
  mkdir,
  readFile,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import {
  assertPortAvailable,
  defaultBinary,
  parseOptions,
  runIntegration,
  stopProcess,
  writeFixtureStore,
} from "./verify-harness-connector.mjs";

test("accepts a repository and explicit executable/output paths containing spaces", () => {
  const options = parseOptions([
    "a harness",
    "--binary",
    "a build/switch.exe",
    "--output-dir",
    "an output",
    "--timeout-ms",
    "1500",
  ]);
  assert.equal(options.harness, resolve("a harness"));
  assert.equal(options.binary, resolve("a build/switch.exe"));
  assert.equal(options.outputDir, resolve("an output"));
  assert.equal(options.timeoutMs, 1500);
});

test("selects native executable locations and rejects incomplete/unknown options", () => {
  assert.match(
    defaultBinary("win32"),
    /target[/\\]debug[/\\]hq-model-switch\.exe$/,
  );
  assert.match(defaultBinary("linux"), /target[/\\]debug[/\\]hq-model-switch$/);
  assert.match(
    defaultBinary("darwin"),
    /Huaqing Switch\.app[/\\]Contents[/\\]MacOS[/\\]hq-model-switch$/,
  );
  assert.throws(() => parseOptions([]), /exactly one/);
  assert.throws(() => parseOptions(["one", "two"]), /exactly one/);
  assert.throws(
    () => parseOptions(["one", "--timeout-ms", "NaN"]),
    /timeout-ms/,
  );
  assert.throws(
    () => parseOptions(["one", "--timeout-ms", "999"]),
    /timeout-ms/,
  );
  assert.throws(() => parseOptions(["one", "--unknown"]), /Unknown option/);
  assert.deepEqual(parseOptions(["--help"]), { help: true });
});

test("refuses an occupied port while leaving the existing listener usable", async () => {
  const existing = createServer((socket) => socket.end("still running"));
  await new Promise((resolve, reject) => {
    existing.once("error", reject);
    existing.listen(0, "127.0.0.1", resolve);
  });
  const port = existing.address().port;
  try {
    await assert.rejects(assertPortAvailable(port), /existing service/);
    assert.equal(existing.listening, true);
  } finally {
    await new Promise((resolve) => existing.close(resolve));
  }
  await assertPortAvailable(port);
});

test("writes and replaces a fixture store without Python or external SQLite", async () => {
  const root = await mkdtemp(join(tmpdir(), "huaqing-script-sqlite-"));
  const path = join(root, "fixture.db");
  try {
    writeFixtureStore(path, { revision: 1, connections: [] });
    writeFixtureStore(path, { revision: 2, connections: [{ id: "fixture" }] });
    const database = new DatabaseSync(path);
    try {
      const rows = database
        .prepare("SELECT value FROM settings WHERE key=?")
        .all("hq.connections.v1");
      assert.equal(rows.length, 1);
      assert.deepEqual(JSON.parse(rows[0].value), {
        revision: 2,
        connections: [{ id: "fixture" }],
      });
    } finally {
      database.close();
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("waits for an owned child to exit, including a child ignoring graceful shutdown", async () => {
  const child = spawn(
    process.execPath,
    [
      "-e",
      "process.on('SIGINT',()=>{});setInterval(()=>{},1000);console.log('ready')",
    ],
    {
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  child.once("error", () => {});
  try {
    await once(child.stdout, "data");
    await stopProcess(child, 30);
    assert(child.exitCode !== null || child.signalCode !== null);
    await stopProcess(child, 30);
  } finally {
    if (child.exitCode === null && child.signalCode === null)
      child.kill("SIGKILL");
  }
});

test("creates a missing output directory and reports early native exit without hanging", async () => {
  const root = await mkdtemp(join(tmpdir(), "huaqing-script-start-"));
  const outputDir = join(root, "new output", "nested");
  try {
    for (const name of [
      "build/harness-node-entry.mjs",
      "packages/hqharness-model-access/index.js",
      "node_modules/@deepseek-ai/dsh-llm/lib/index.js",
    ]) {
      const path = join(root, name);
      await mkdir(resolve(path, ".."), { recursive: true });
      await writeFile(path, "// fixture only\n");
    }
    await assert.rejects(
      runIntegration({
        harness: root,
        binary: process.execPath,
        outputDir,
        timeoutMs: 3000,
      }),
      /Switch exited before becoming ready/,
    );
    assert((await stat(outputDir)).isDirectory());
    assert.equal(
      await readFile(join(outputDir, "huaqing-native-e2e.log"), "utf8"),
      "",
    );
    await assertPortAvailable();
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a whole-run deadline stops the native process and invalidates an older success report", async () => {
  const root = await mkdtemp(join(tmpdir(), "huaqing-script-timeout-"));
  const previousNodeOptions = process.env.NODE_OPTIONS;
  const outputDir = join(root, "output");
  const pidPath = join(root, "native.pid");
  try {
    for (const name of [
      "build/harness-node-entry.mjs",
      "packages/hqharness-model-access/index.js",
      "node_modules/@deepseek-ai/dsh-llm/lib/index.js",
    ]) {
      const path = join(root, name);
      await mkdir(resolve(path, ".."), { recursive: true });
      await writeFile(path, "// fixture only\n");
    }
    await mkdir(outputDir);
    await writeFile(
      join(outputDir, "huaqing-harness-e2e.json"),
      '{"passed":true}',
    );
    const preload = join(root, "keep-alive.cjs");
    await writeFile(
      preload,
      `require('node:fs').writeFileSync(${JSON.stringify(pidPath)},String(process.pid));setInterval(()=>{},1000);`,
    );
    process.env.NODE_OPTIONS = `--require=${JSON.stringify(preload)}`;
    await assert.rejects(
      runIntegration({
        harness: root,
        binary: process.execPath,
        outputDir,
        timeoutMs: 1000,
      }),
      /Connector acceptance timed out/,
    );
    const pid = Number(await readFile(pidPath, "utf8"));
    assert.throws(() => process.kill(pid, 0), { code: "ESRCH" });
    await assert.rejects(stat(join(outputDir, "huaqing-harness-e2e.json")), {
      code: "ENOENT",
    });
    await assertPortAvailable();
  } finally {
    if (previousNodeOptions === undefined) delete process.env.NODE_OPTIONS;
    else process.env.NODE_OPTIONS = previousNodeOptions;
    await rm(root, { recursive: true, force: true });
  }
});
