// Real native Switch + real Harness/Pi adapters, against a deterministic local provider.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn, spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { openSync, closeSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
if (!process.argv[2])
  throw new Error(
    "Usage: node scripts/verify-harness-connector.mjs <HQHarness repository>",
  );
const harness = resolve(process.argv[2]);
const binary = resolve(
  root,
  "src-tauri/target/debug/bundle/macos/Huaqing Switch.app/Contents/MacOS/hq-model-switch",
);
const home = await mkdtemp(join(tmpdir(), "huaqing-integration-"));
const logPath = join(root, "validation/huaqing-native-e2e.log");
const calls = [];
let cancelled = false,
  native,
  child,
  oldToken;
const ids = [randomUUID(), randomUUID(), randomUUID(), randomUUID()];
const fixture = createServer(async (req, res) => {
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
await new Promise((resolve) => fixture.listen(0, "127.0.0.1", resolve));
const upstream = `http://127.0.0.1:${fixture.address().port}`;
const store = {
  revision: 1,
  activeId: ids[0],
  connections: ["chat", "responses", "anthropic", "redirect"].map(
    (name, i) => ({
      id: ids[i],
      name,
      protocol:
        i === 1 ? "openai-responses" : i === 2 ? "anthropic" : "openai-chat",
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
function writeStore() {
  const result = spawnSync(
    "python3",
    [
      "-c",
      `import sqlite3,sys,json
p=json.load(sys.stdin);c=sqlite3.connect(p['db']);c.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)');c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('hq.connections.v1',json.dumps(p['store'])));c.commit();c.close()`,
    ],
    { input: JSON.stringify({ db, store }), encoding: "utf8" },
  );
  assert.equal(result.status, 0, result.stderr);
}
writeStore();
const connectorPath = join(home, ".huaqing-switch/connector.json");
async function waitUntil(check, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await check()) return;
    await delay(50);
  }
  throw new Error("Integration wait timed out");
}
async function startNative() {
  const fd = openSync(logPath, "a");
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
  closeSync(fd);
  await waitUntil(async () => {
    try {
      const c = JSON.parse(await readFile(connectorPath, "utf8"));
      if (c.token === oldToken) return false;
      const r = await fetch(c.endpoint + "/hq/manifest", {
        headers: { Authorization: "Bearer " + c.token },
      });
      return r.ok;
    } catch {
      return false;
    }
  }, 30000);
  return JSON.parse(await readFile(connectorPath, "utf8"));
}
async function stopNative() {
  if (native && native.exitCode === null) {
    const p = native;
    const done = new Promise((r) => p.once("exit", r));
    p.kill("SIGINT");
    await Promise.race([done, delay(5000)]);
    if (p.exitCode === null && p.signalCode === null) p.kill("SIGKILL");
  }
  native = undefined;
}
const modelId = (i) => `hq-${ids[i]}-v1`;
const entry = join(home, "harness-test.mjs");
const moduleUrl = (name) =>
  pathToFileURL(join(harness, "node_modules", name, "lib/index.js")).href;
const config = join(home, "cordis.json");
await writeFile(
  config,
  JSON.stringify([
    { id: "llm", name: moduleUrl("@deepseek-ai/dsh-llm") },
    {
      id: "switch",
      name: pathToFileURL(
        join(harness, "packages/hqharness-model-access/index.js"),
      ).href,
    },
  ]),
);
await writeFile(
  entry,
  `
 import assert from 'node:assert/strict';import {setTimeout as delay} from 'node:timers/promises';import {boot} from ${JSON.stringify(moduleUrl("@deepseek-ai/dsh-app-boot"))};
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
try {
  const connector = await startNative();
  oldToken = connector.token;
  const headers = {
    Authorization: "Bearer " + connector.token,
    "Content-Type": "application/json",
  };
  assert.equal((await fetch(connector.endpoint + "/hq/manifest")).status, 401);
  assert.equal(
    (
      await fetch(connector.endpoint + "/hq/manifest", {
        headers: { ...headers, Origin: "https://hostile.invalid" },
      })
    ).status,
    403,
  );
  const route = `${connector.endpoint}/hq/connections/${ids[0]}/1/v1/chat/completions`;
  assert.equal(
    (
      await fetch(route, {
        method: "POST",
        headers,
        body: JSON.stringify({ model: "wrong" }),
      })
    ).status,
    400,
  );
  assert.equal(
    (
      await fetch(route, {
        method: "POST",
        headers,
        body: JSON.stringify({ model: modelId(0), max_tokens: 999999 }),
      })
    ).status,
    400,
  );
  assert.equal(
    (
      await fetch(
        `${connector.endpoint}/hq/connections/${ids[3]}/1/v1/chat/completions`,
        {
          method: "POST",
          headers,
          body: JSON.stringify({ model: modelId(3) }),
        },
      )
    ).status,
    302,
  );
  let output = "";
  const phases = [];
  child = spawn(
    process.execPath,
    [join(harness, "build/harness-node-entry.mjs"), entry],
    {
      cwd: home,
      env: {
        ...process.env,
        DSH_HOME: home,
        HQHARNESS_MODEL_SOURCE: "huaqing-switch",
        HUAQING_SWITCH_CONNECTOR_PATH: connectorPath,
        NO_PROXY: "127.0.0.1,localhost",
      },
      stdio: ["ignore", "pipe", "pipe", "ipc"],
    },
  );
  child.stdout.on("data", (chunk) => (output += chunk));
  child.stderr.on("data", (chunk) => (output += chunk));
  let phaseError;
  child.on("message", async (value) => {
    try {
      phases.push(value.phase);
      if (value.phase === "base") {
        await waitUntil(() => cancelled);
        await stopNative();
        child.send("offline");
      } else if (value.phase === "offline") {
        const next = await startNative();
        assert.notEqual(next.token, oldToken);
        assert.equal(
          (
            await fetch(next.endpoint + "/hq/manifest", {
              headers: { Authorization: "Bearer " + oldToken },
            })
          ).status,
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
          (
            await fetch(route, {
              method: "POST",
              headers: { ...headers, Authorization: "Bearer " + latest.token },
              body: JSON.stringify({ model: modelId(0) }),
            })
          ).status,
          409,
        );
        child.send("edited");
      }
    } catch (error) {
      phaseError = error;
      child.kill("SIGTERM");
    }
  });
  const result = await Promise.race([
    new Promise((resolve) =>
      child.once("exit", (code, signal) => resolve({ code, signal })),
    ),
    delay(60000, undefined, { ref: false }).then(() => {
      child.kill("SIGTERM");
      throw new Error("Harness integration timeout");
    }),
  ]);
  if (phaseError) throw phaseError;
  assert.equal(result.code, 0, output);
  assert(output.includes("HUAQING_HARNESS_E2E_PASS"), output);
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
    phases,
    protocols: ["openai-chat", "openai-responses", "anthropic"],
    upstreamRequests: calls.length,
    cancellationPropagated: cancelled,
    credentialRotation: true,
    defaultSwitchPreservesPinnedModel: true,
    editedModelRejected: true,
    legacyProviderBlocked: true,
    scope:
      "Real native Switch and real Harness/Pi runtime; deterministic local API fixture, no paid model call",
  };
  await writeFile(
    join(root, "validation/huaqing-harness-e2e.json"),
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify(report));
} finally {
  if (child && child.exitCode === null && child.signalCode === null)
    child.kill("SIGTERM");
  await stopNative();
  fixture.closeAllConnections();
  await new Promise((resolve) => fixture.close(resolve));
  await rm(home, { recursive: true, force: true });
}
