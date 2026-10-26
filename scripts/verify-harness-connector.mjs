// Real native Switch + real Harness/Pi adapters, against a deterministic local provider.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createServer as createPortProbe } from "node:net";
import { parseArgs } from "node:util";
import { DatabaseSync } from "node:sqlite";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import {
  access,
  mkdtemp,
  mkdir,
  readFile,
  stat,
  writeFile,
  rm,
} from "node:fs/promises";
import { openSync, closeSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join, dirname, basename } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const endpoint = "http://127.0.0.1:17621";

/** Resolve CLI paths without requiring Python or a platform-specific shell. */
export function parseOptions(args, platform = process.platform) {
  const { values, positionals } = parseArgs({
    args,
    options: {
      binary: { type: "string" },
      "harness-dir": { type: "string" },
      "harness-exe": { type: "string" },
      "harness-app-root": { type: "string" },
      "output-dir": { type: "string" },
      "timeout-ms": { type: "string" },
      help: { type: "boolean" },
    },
    allowPositionals: true,
  });
  if (values.help) return { help: true };
  if (positionals.length > 1 || (positionals.length && values["harness-dir"]))
    throw new Error(
      "Specify exactly one HQHarness repository path, as a positional argument or --harness-dir",
    );
  const harness = positionals[0] ?? values["harness-dir"];
  const harnessExecutable = values["harness-exe"];
  const harnessAppRoot = values["harness-app-root"];
  if (harness && (harnessExecutable || harnessAppRoot))
    throw new Error(
      "Source and installed HQHarness options cannot be combined",
    );
  if (Boolean(harnessExecutable) !== Boolean(harnessAppRoot))
    throw new Error(
      "Installed acceptance requires both --harness-exe and --harness-app-root",
    );
  if (!harness && !harnessExecutable)
    throw new Error(
      "Specify exactly one HQHarness source directory or an installed executable/archive pair; use --help for usage",
    );
  const timeoutMs = Number(values["timeout-ms"] ?? 120000);
  if (
    !Number.isSafeInteger(timeoutMs) ||
    timeoutMs < 1000 ||
    timeoutMs > 600000
  )
    throw new Error("--timeout-ms must be an integer from 1000 to 600000");
  return {
    harness: harness ? resolve(harness) : undefined,
    harnessExecutable: harnessExecutable
      ? resolve(harnessExecutable)
      : undefined,
    harnessAppRoot: harnessAppRoot ? resolve(harnessAppRoot) : undefined,
    binary: values.binary ? resolve(values.binary) : defaultBinary(platform),
    outputDir: resolve(values["output-dir"] ?? join(root, "validation")),
    timeoutMs,
  };
}

/** Resolve one dependency closure without asking external Node to read ASAR internals. */
export function harnessTarget({ harness, harnessExecutable, harnessAppRoot }) {
  if (harnessExecutable || harnessAppRoot) {
    if (!harnessExecutable || !harnessAppRoot || harness)
      throw new Error(
        "Choose either a source directory or both installed HQHarness paths",
      );
    const appRoot = resolve(harnessAppRoot);
    if (basename(appRoot) !== "app.asar")
      throw new Error(
        "--harness-app-root must name the installed resources/app.asar archive",
      );
    const executable = resolve(harnessExecutable);
    const nodeEntry = join(dirname(appRoot), "harness-node-entry.mjs");
    return {
      mode: "installed",
      appRoot,
      executable,
      nodeEntry,
      pluginEntry: join(
        appRoot,
        "node_modules/hqharness-model-access/index.js",
      ),
      inputs: [executable, appRoot, nodeEntry],
    };
  }
  if (!harness) throw new Error("A HQHarness source directory is required");
  const appRoot = resolve(harness);
  const nodeEntry = join(appRoot, "build/harness-node-entry.mjs");
  const pluginEntry = join(appRoot, "packages/hqharness-model-access/index.js");
  return {
    mode: "source",
    appRoot,
    executable: process.execPath,
    nodeEntry,
    pluginEntry,
    inputs: [
      nodeEntry,
      pluginEntry,
      join(appRoot, "node_modules/@deepseek-ai/dsh-llm/lib/index.js"),
    ],
  };
}

/** Launch only the chosen runtime, with an existing neutral cwd and isolated profile. */
export function harnessLaunch(
  target,
  { home, entry, connectorPath, env = process.env },
) {
  return {
    executable: target.executable,
    args: [target.nodeEntry, entry],
    options: {
      cwd: home,
      env: {
        ...env,
        ...(target.mode === "installed" ? { ELECTRON_RUN_AS_NODE: "1" } : {}),
        DSH_HOME: home,
        HQHARNESS_MODEL_SOURCE: "huaqing-switch",
        HUAQING_SWITCH_CONNECTOR_PATH: connectorPath,
        NO_PROXY: "127.0.0.1,localhost",
        no_proxy: "127.0.0.1,localhost",
      },
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe", "ipc"],
    },
  };
}

/** The Tauri debug output for each native platform; packaged builds use --binary. */
export function defaultBinary(platform) {
  return platform === "darwin"
    ? join(
        root,
        "src-tauri/target/debug/bundle/macos/Huaqing Switch.app/Contents/MacOS/hq-model-switch",
      )
    : join(
        root,
        "src-tauri/target/debug",
        platform === "win32" ? "hq-model-switch.exe" : "hq-model-switch",
      );
}

/** Refuse to take over the user's running Switch or any other listener. */
export async function assertPortAvailable(port = 17621) {
  const probe = createPortProbe();
  await new Promise((resolve, reject) => {
    probe.once("error", (error) =>
      reject(
        new Error(
          error.code === "EADDRINUSE"
            ? `Cannot reserve 127.0.0.1:${port} for isolated acceptance (${error.code}); stop the existing service explicitly before testing`
            : `Cannot reserve 127.0.0.1:${port} for isolated acceptance (${error.code}); check local loopback permissions`,
          { cause: error },
        ),
      ),
    );
    probe.listen({ host: "127.0.0.1", port, exclusive: true }, resolve);
  });
  await new Promise((resolve, reject) =>
    probe.close((error) => (error ? reject(error) : resolve())),
  );
}

/** Seed or update only the temporary test database using Node 24's SQLite. */
export function writeFixtureStore(db, store) {
  const connection = new DatabaseSync(db, { timeout: 5000 });
  try {
    connection.exec(
      "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)",
    );
    connection
      .prepare("INSERT OR REPLACE INTO settings VALUES(?,?)")
      .run("hq.connections.v1", JSON.stringify(store));
  } finally {
    connection.close();
  }
}

/** Stop an owned child and confirm exit before removing its temporary files. */
export async function stopProcess(child, graceMs = 5000) {
  if (
    !child ||
    !child.pid ||
    child.exitCode !== null ||
    child.signalCode !== null
  )
    return;
  let closed = false;
  const done = new Promise((resolve) =>
    child.once("close", () => {
      closed = true;
      resolve();
    }),
  );
  child.kill(process.platform === "win32" ? "SIGTERM" : "SIGINT");
  await Promise.race([done, delay(graceMs, undefined, { ref: false })]);
  if (!closed) {
    child.kill("SIGKILL");
    await Promise.race([done, delay(5000, undefined, { ref: false })]);
    if (!closed)
      throw new Error(
        `Owned acceptance process ${child.pid} did not exit; its temporary files were preserved`,
      );
  }
}

/** Run native Switch and the real Harness/Pi adapters with disposable fixture data. */
export async function runIntegration({
  harness,
  harnessExecutable,
  harnessAppRoot,
  binary,
  outputDir,
  timeoutMs,
}) {
  if (Number(process.versions.node.split(".")[0]) < 24)
    throw new Error(
      "The connector acceptance requires Node.js 24 or later (matching HQHarness)",
    );
  const target = harnessTarget({ harness, harnessExecutable, harnessAppRoot });
  const controller = new AbortController();
  const interrupted = () =>
    controller.abort(new Error("Connector acceptance interrupted"));
  const timer = setTimeout(
    () => controller.abort(new Error("Connector acceptance timed out")),
    timeoutMs,
  );
  timer.unref();
  process.once("SIGINT", interrupted);
  process.once("SIGTERM", interrupted);
  let home, fixture, native, child, oldToken, nativeError;
  let phaseWork = Promise.resolve();
  const calls = [];
  let cancelled = false;
  const request = (url, options = {}) =>
    fetch(url, {
      ...options,
      redirect: "manual",
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]),
    });
  const status = async (url, options) => {
    const response = await request(url, options);
    try {
      return response.status;
    } finally {
      await response.body?.cancel();
    }
  };
  try {
    await assertPortAvailable();
    for (const path of [binary, ...target.inputs])
      await access(path).catch((error) => {
        throw new Error(`Missing acceptance input: ${path}`, { cause: error });
      });
    if (target.mode === "installed" && !(await stat(target.appRoot)).isFile())
      throw new Error(
        "Installed acceptance requires an actual app.asar file, not a source directory",
      );
    await mkdir(outputDir, { recursive: true });
    await rm(join(outputDir, "huaqing-harness-e2e.json"), { force: true });
    home = await mkdtemp(join(tmpdir(), "huaqing-integration-"));
    const logPath = join(outputDir, "huaqing-native-e2e.log");
    const ids = [randomUUID(), randomUUID(), randomUUID(), randomUUID()];
    fixture = createServer(async (req, res) => {
      let text = "";
      for await (const chunk of req) text += chunk;
      const body = JSON.parse(text || "{}");
      calls.push({ url: req.url, body, headers: req.headers });
      const route = req.url?.split("/")[1];
      if (route === "redirect") {
        res.writeHead(302, { location: "/stolen" }).end();
        return;
      }
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      const emit = (value, event) =>
        res.write(
          `${event ? "event: " + event + "\n" : ""}data: ${JSON.stringify(value)}\n\n`,
        );
      if (route === "chat") {
        const base = {
          id: "chat-test",
          object: "chat.completion.chunk",
          created: 1,
          model: "upstream-chat",
        };
        if (text.includes("CANCEL_PROBE")) {
          emit({
            ...base,
            choices: [
              {
                index: 0,
                delta: { role: "assistant", content: "waiting" },
                finish_reason: null,
              },
            ],
          });
          res.on("close", () => {
            cancelled = true;
          });
          return;
        }
        emit({
          ...base,
          choices: [
            {
              index: 0,
              delta: {
                role: "assistant",
                tool_calls: [
                  {
                    index: 0,
                    id: "call_1",
                    type: "function",
                    function: {
                      name: "browser_use",
                      arguments: '{"action":"status"}',
                    },
                  },
                ],
              },
              finish_reason: null,
            },
          ],
        });
        emit({
          ...base,
          choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }],
          usage: { prompt_tokens: 3, completion_tokens: 5, total_tokens: 8 },
        });
        res.end("data: [DONE]\n\n");
      } else if (route === "responses") {
        const item = {
          type: "function_call",
          id: "fc_1",
          call_id: "call_1",
          name: "browser_use",
          arguments: '{"action":"status"}',
          status: "completed",
        };
        emit(
          {
            type: "response.created",
            response: {
              id: "resp_1",
              created_at: 1,
              status: "in_progress",
              output: [],
              model: "upstream-responses",
            },
          },
          "response.created",
        );
        emit(
          {
            type: "response.output_item.added",
            output_index: 0,
            item: { ...item, arguments: "", status: "in_progress" },
          },
          "response.output_item.added",
        );
        emit(
          {
            type: "response.function_call_arguments.delta",
            item_id: "fc_1",
            output_index: 0,
            delta: item.arguments,
          },
          "response.function_call_arguments.delta",
        );
        emit(
          {
            type: "response.function_call_arguments.done",
            item_id: "fc_1",
            output_index: 0,
            arguments: item.arguments,
          },
          "response.function_call_arguments.done",
        );
        emit(
          { type: "response.output_item.done", output_index: 0, item },
          "response.output_item.done",
        );
        emit(
          {
            type: "response.completed",
            response: {
              id: "resp_1",
              created_at: 1,
              status: "completed",
              model: "upstream-responses",
              output: [item],
              usage: {
                input_tokens: 3,
                output_tokens: 5,
                total_tokens: 8,
                input_tokens_details: { cached_tokens: 0 },
                output_tokens_details: { reasoning_tokens: 0 },
              },
            },
          },
          "response.completed",
        );
        res.end();
      } else if (route === "anthropic") {
        emit(
          {
            type: "message_start",
            message: {
              id: "msg_1",
              type: "message",
              role: "assistant",
              content: [],
              model: "upstream-anthropic",
              stop_reason: null,
              stop_sequence: null,
              usage: {
                input_tokens: 3,
                output_tokens: 0,
                cache_creation_input_tokens: 0,
                cache_read_input_tokens: 0,
              },
            },
          },
          "message_start",
        );
        emit(
          {
            type: "content_block_start",
            index: 0,
            content_block: {
              type: "tool_use",
              id: "call_1",
              name: "browser_use",
              input: {},
            },
          },
          "content_block_start",
        );
        emit(
          {
            type: "content_block_delta",
            index: 0,
            delta: {
              type: "input_json_delta",
              partial_json: '{"action":"status"}',
            },
          },
          "content_block_delta",
        );
        emit({ type: "content_block_stop", index: 0 }, "content_block_stop");
        emit(
          {
            type: "message_delta",
            delta: { stop_reason: "tool_use", stop_sequence: null },
            usage: { output_tokens: 5 },
          },
          "message_delta",
        );
        emit({ type: "message_stop" }, "message_stop");
        res.end();
      } else {
        res.end();
      }
    });
    await new Promise((resolve, reject) => {
      fixture.once("error", reject);
      fixture.listen(0, "127.0.0.1", resolve);
    });
    const upstream = `http://127.0.0.1:${fixture.address().port}`;
    const store = {
      revision: 1,
      activeId: ids[0],
      connections: ["chat", "responses", "anthropic", "redirect"].map(
        (name, i) => ({
          id: ids[i],
          name,
          protocol:
            i === 1
              ? "openai-responses"
              : i === 2
                ? "anthropic"
                : "openai-chat",
          baseUrl: `${upstream}/${name}${i === 2 ? "" : "/v1"}`,
          model: `upstream-${name}`,
          apiKey: `fixture-key-${name}`,
          revision: 1,
          contextWindow: 32768,
          maxOutputTokens: 4096,
          supportsImages: false,
        }),
      ),
    };
    const db = join(home, ".hq-model-switch/hq-model-switch.db");
    await mkdir(dirname(db), { recursive: true, mode: 0o700 });
    const writeStore = () => writeFixtureStore(db, store);
    writeStore();
    const connectorPath = join(home, ".huaqing-switch/connector.json");
    async function waitUntil(check, timeout = 15000) {
      const end = Date.now() + timeout;
      while (Date.now() < end) {
        controller.signal.throwIfAborted();
        if (await check()) return;
        await delay(50, undefined, { signal: controller.signal });
      }
      throw new Error("Integration wait timed out");
    }
    async function startNative() {
      await assertPortAvailable();
      const fd = openSync(logPath, "a");
      nativeError = undefined;
      try {
        native = spawn(binary, [], {
          cwd: home,
          env: {
            ...process.env,
            CC_SWITCH_TEST_HOME: home,
            NO_PROXY: "127.0.0.1,localhost",
            no_proxy: "127.0.0.1,localhost",
          },
          stdio: ["ignore", fd, fd],
        });
        native.once("error", (error) => {
          nativeError = error;
        });
      } finally {
        closeSync(fd);
      }
      await waitUntil(async () => {
        if (nativeError)
          throw new Error(`Switch failed to start: ${nativeError.message}`);
        if (native.exitCode !== null || native.signalCode !== null)
          throw new Error(
            `Switch exited before becoming ready; inspect ${logPath}`,
          );
        try {
          const c = JSON.parse(await readFile(connectorPath, "utf8"));
          if (c.token === oldToken) return false;
          assert.equal(c.endpoint, endpoint);
          assert.equal(c.product, "huaqing-switch");
          assert.match(c.token, /^[a-f0-9]{64}$/);
          return (
            (await status(endpoint + "/hq/manifest", {
              headers: { Authorization: "Bearer " + c.token },
            })) === 200
          );
        } catch {
          return false;
        }
      }, 30000);
      return JSON.parse(await readFile(connectorPath, "utf8"));
    }
    async function stopNative() {
      await stopProcess(native);
      native = undefined;
    }
    const modelId = (i) => `hq-${ids[i]}-v1`;
    const entry = join(home, "harness-test.mjs");
    const moduleUrl = (name) =>
      pathToFileURL(join(target.appRoot, "node_modules", name, "lib/index.js"))
        .href;
    const config = join(home, "cordis.json");
    await writeFile(
      config,
      JSON.stringify([
        { id: "llm", name: moduleUrl("@deepseek-ai/dsh-llm") },
        {
          id: "switch",
          name: pathToFileURL(target.pluginEntry).href,
        },
      ]),
    );
    await writeFile(
      entry,
      `
 import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';import {setTimeout as delay} from 'node:timers/promises';
 const mode=${JSON.stringify(target.mode)};
 if(mode==='installed')assert(process.versions.electron,'Installed acceptance must execute the installed Electron runtime, not standalone Node');
 const desktop=JSON.parse(await readFile(${JSON.stringify(join(target.appRoot, "package.json"))},'utf8'));
 process.send({kind:'harness-runtime',runtime:{mode,electron:process.versions.electron??null,node:process.versions.node,platform:process.platform,architecture:process.arch,executable:process.execPath,appRoot:${JSON.stringify(target.appRoot)},desktopVersion:desktop.version}});
 const {boot}=await import(${JSON.stringify(moduleUrl("@deepseek-ai/dsh-app-boot"))});
 let selection={provider:'old-provider',model:'old-model'};const defaults=[];
 const ctx=await boot('huaqing-integration',${JSON.stringify(config)},[],ctx=>{ctx.provide('connection',{fetch:{register(){return()=>{}}}});ctx.provide('agentDefaultModel',{currentSelection:()=>selection,async saveSelection(value){selection=value;defaults.push(value)}})});
 const wait=async(check)=>{for(let i=0;i<150;i++){if(check())return;await delay(50)}throw new Error('Harness synchronization timeout')};
 const message=()=>new Promise(resolve=>process.once('message',resolve));
 const invoke=async(model,text='Check browser')=>{const chunks=[];for await(const c of ctx.llm.stream({provider:'huaqing-switch',model,messages:[{role:'user',content:[{type:'text',text}]}],tools:[{name:'browser_use',description:'Browser action',parameters:{type:'object',properties:{action:{type:'string'}}}}]}))chunks.push(c);assert(!chunks.some(c=>c.type==='error'),JSON.stringify(chunks));assert(JSON.stringify(chunks).includes('browser_use'),JSON.stringify(chunks));return chunks};
 try{
  await wait(()=>ctx.llm.listProviders().some(p=>p.id==='huaqing-switch'));await wait(()=>selection.provider==='huaqing-switch');
  const models=await ctx.llm.listModels('huaqing-switch');assert.equal(models.length,4);assert.equal(selection.model,${JSON.stringify(modelId(0))});
  for(const model of ${JSON.stringify([modelId(0), modelId(1), modelId(2)])})await invoke(model);
  ctx.llm.registerAdapter(['legacy'],{providerInfo:()=>({id:'legacy',name:'Legacy'}),providerRetryPolicy:()=>undefined});
  assert(!ctx.llm.listProviders().some(p=>p.id==='legacy'));await assert.rejects(ctx.llm.resolveModelInfo('legacy','model'),e=>e.code==='HUAQING_SWITCH_REQUIRED');
  const abort=new AbortController();let received=false;for await(const c of ctx.llm.stream({provider:'huaqing-switch',model:${JSON.stringify(modelId(0))},signal:abort.signal,messages:[{role:'user',content:[{type:'text',text:'CANCEL_PROBE'}]}]})){if(JSON.stringify(c).includes('waiting')){received=true;abort.abort();break}}assert(received);
  let incoming=message();process.send({phase:'base'});assert.equal(await incoming,'offline');
  await assert.rejects(ctx.llm.resolveModelInfo('huaqing-switch',${JSON.stringify(modelId(0))}),e=>e.code==='HUAQING_SWITCH_UNAVAILABLE');
  incoming=message();process.send({phase:'offline'});assert.equal(await incoming,'restarted');await invoke(${JSON.stringify(modelId(0))});
  incoming=message();process.send({phase:'restart'});assert.equal(await incoming,'default-changed');
  await wait(()=>selection.model===${JSON.stringify(modelId(1))});await invoke(${JSON.stringify(modelId(0))});
  incoming=message();process.send({phase:'pinned'});assert.equal(await incoming,'edited');
  await assert.rejects(ctx.llm.resolveModelInfo('huaqing-switch',${JSON.stringify(modelId(0))}),e=>e.code==='UNKNOWN_MODEL');
  process.send({phase:'complete',defaults});console.log('HUAQING_HARNESS_E2E_PASS');
 }finally{await ctx.fiber.dispose()}
`,
    );
    const connector = await startNative();
    oldToken = connector.token;
    const headers = {
      Authorization: "Bearer " + connector.token,
      "Content-Type": "application/json",
    };
    assert.equal(await status(endpoint + "/hq/manifest"), 401);
    assert.equal(
      await status(connector.endpoint + "/hq/manifest", {
        headers: { ...headers, Origin: "https://hostile.invalid" },
      }),
      403,
    );
    const route = `${connector.endpoint}/hq/connections/${ids[0]}/1/v1/chat/completions`;
    assert.equal(
      await status(route, {
        method: "POST",
        headers,
        body: JSON.stringify({ model: "wrong" }),
      }),
      400,
    );
    assert.equal(
      await status(route, {
        method: "POST",
        headers,
        body: JSON.stringify({ model: modelId(0), max_tokens: 999999 }),
      }),
      400,
    );
    assert.equal(
      await status(
        `${connector.endpoint}/hq/connections/${ids[3]}/1/v1/chat/completions`,
        {
          method: "POST",
          headers,
          body: JSON.stringify({ model: modelId(3) }),
        },
      ),
      302,
    );
    let output = "";
    const phases = [];
    let harnessRuntime;
    const launch = harnessLaunch(target, { home, entry, connectorPath });
    child = spawn(launch.executable, launch.args, launch.options);
    const childClosed = new Promise((resolve) => {
      child.once("error", (error) =>
        resolve({
          error: new Error(
            `Could not start ${target.mode} HQHarness runtime (${error.code}) from neutral directory ${home}: ${error.message}`,
            { cause: error },
          ),
        }),
      );
      child.once("close", (code, signal) => resolve({ code, signal }));
    });
    child.stdout.on(
      "data",
      (chunk) => (output = (output + chunk).slice(-65536)),
    );
    child.stderr.on(
      "data",
      (chunk) => (output = (output + chunk).slice(-65536)),
    );
    let phaseError;
    child.on("message", (value) => {
      phaseWork = phaseWork
        .then(async () => {
          controller.signal.throwIfAborted();
          try {
            if (value.kind === "harness-runtime") {
              assert.equal(value.runtime.mode, target.mode);
              if (target.mode === "installed") assert(value.runtime.electron);
              harnessRuntime = value.runtime;
              return;
            }
            phases.push(value.phase);
            if (value.phase === "base") {
              await waitUntil(() => cancelled);
              await stopNative();
              child.send("offline");
            } else if (value.phase === "offline") {
              const next = await startNative();
              assert.notEqual(next.token, oldToken);
              assert.equal(
                await status(next.endpoint + "/hq/manifest", {
                  headers: { Authorization: "Bearer " + oldToken },
                }),
                401,
              );
              child.send("restarted");
            } else if (value.phase === "restart") {
              store.revision++;
              store.activeId = ids[1];
              writeStore();
              child.send("default-changed");
            } else if (value.phase === "pinned") {
              store.revision++;
              store.connections[0].revision++;
              writeStore();
              const latest = JSON.parse(await readFile(connectorPath, "utf8"));
              assert.equal(
                await status(route, {
                  method: "POST",
                  headers: {
                    ...headers,
                    Authorization: "Bearer " + latest.token,
                  },
                  body: JSON.stringify({ model: modelId(0) }),
                }),
                409,
              );
              child.send("edited");
            }
          } catch (error) {
            phaseError = error;
            controller.abort(error);
            child.kill("SIGTERM");
          }
        })
        .catch((error) => {
          phaseError = error;
          controller.abort(error);
          child.kill("SIGTERM");
        });
    });
    const result = await Promise.race([
      childClosed,
      new Promise((_, reject) => {
        if (controller.signal.aborted) reject(controller.signal.reason);
        else
          controller.signal.addEventListener(
            "abort",
            () => reject(controller.signal.reason),
            { once: true },
          );
      }),
    ]);
    await phaseWork;
    if (result.error) throw result.error;
    if (phaseError) throw phaseError;
    assert.equal(result.code, 0, output);
    assert(output.includes("HUAQING_HARNESS_E2E_PASS"), output);
    assert(
      harnessRuntime,
      "The selected HQHarness runtime did not report its identity",
    );
    for (const call of calls) {
      const route = call.url.split("/")[1];
      assert.equal(call.body.model, `upstream-${route}`);
      assert.equal(
        route === "anthropic"
          ? call.headers["x-api-key"]
          : call.headers.authorization,
        route === "anthropic"
          ? `fixture-key-${route}`
          : `Bearer fixture-key-${route}`,
      );
      assert(!JSON.stringify(call.headers).includes(oldToken));
    }
    assert(cancelled);
    assert(!calls.some((call) => call.url === "/stolen"));
    const report = {
      passed: true,
      platform: process.platform,
      architecture: process.arch,
      nodeVersion: process.version,
      harnessRuntime,
      phases,
      protocols: ["openai-chat", "openai-responses", "anthropic"],
      upstreamRequests: calls.length,
      cancellationPropagated: cancelled,
      credentialRotation: true,
      defaultSwitchPreservesPinnedModel: true,
      editedModelRejected: true,
      legacyProviderBlocked: true,
      scope: `Real native Switch and ${target.mode === "installed" ? "installed HQHarness Electron/Pi" : "source Harness/Pi"} runtime; deterministic local API fixture, no paid model call; GUI not exercised`,
    };
    await writeFile(
      join(outputDir, "huaqing-harness-e2e.json"),
      JSON.stringify(report, null, 2),
    );
    console.log(JSON.stringify(report));
  } catch (error) {
    if (controller.signal.aborted) throw controller.signal.reason;
    throw error;
  } finally {
    controller.abort(new Error("Connector acceptance cleanup"));
    clearTimeout(timer);
    process.removeListener("SIGINT", interrupted);
    process.removeListener("SIGTERM", interrupted);
    const cleanup = await Promise.allSettled([stopProcess(child), phaseWork]);
    const stop = await Promise.allSettled([stopProcess(native)]);
    if (fixture?.listening) {
      fixture.closeAllConnections();
      await new Promise((resolve) => fixture.close(resolve));
    }
    const failed = [...cleanup, ...stop].find(
      (result) => result.status === "rejected",
    );
    if (failed) throw failed.reason;
    if (home) await rm(home, { recursive: true, force: true });
  }
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  try {
    const options = parseOptions(process.argv.slice(2));
    if (options.help)
      console.log(`Usage: node scripts/verify-harness-connector.mjs <HQHarness repository> [options]
   or: node scripts/verify-harness-connector.mjs --harness-exe <installed executable> --harness-app-root <resources/app.asar> [options]

Requires Node.js 24+ and native Switch. Source mode needs HQHarness dependencies.
  --harness-dir <path>  Source checkout (alternative to the positional argument)
  --harness-exe <path>  Installed Electron executable in Node mode (Windows: HQHarness.exe)
  --harness-app-root <path>  Matching installed resources/app.asar archive
  --binary <path>       Native Switch executable (default: platform debug build)
  --output-dir <path>   Logs and JSON report (default: validation/)
  --timeout-ms <ms>     Whole-run deadline, 1000..600000 (default: 120000)

Uses a disposable home/profile and local fixture keys. Port 17621 must be free.
Existing services are never stopped. No paid model requests are made.`);
    else await runIntegration(options);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
