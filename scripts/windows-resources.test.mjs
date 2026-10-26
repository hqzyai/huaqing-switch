import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { dirname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const nativeRoot = fileURLToPath(new URL("../src-tauri/", import.meta.url));
const base = JSON.parse(
  await readFile(resolve(nativeRoot, "tauri.conf.json"), "utf8"),
);
const windows = JSON.parse(
  await readFile(resolve(nativeRoot, "tauri.windows.conf.json"), "utf8"),
);
const fragmentPath = resolve(nativeRoot, "wix/per-user-resources.wxs");
const attributes = (tag) =>
  Object.fromEntries(
    [...tag.matchAll(/([A-Za-z]+)="([^"]*)"/g)].map((m) => [m[1], m[2]]),
  );

test("Windows replaces generated resources while other platforms retain their resources", () => {
  assert.deepEqual(base.bundle.resources, [
    "../LICENSE",
    "../UPSTREAM.md",
    "../brand.config.json",
  ]);
  assert.deepEqual(windows.bundle?.resources, []);
  assert.deepEqual(windows.bundle.windows.wix.fragmentPaths, [
    "wix/per-user-resources.wxs",
  ]);
  assert.deepEqual(windows.bundle.windows.wix.componentGroupRefs, [
    "HqPerUserResources",
  ]);
});

test("each bundled resource uses an HKCU key path and preserves its target directory", async () => {
  const fragment = await readFile(fragmentPath, "utf8");
  const components = [
    ...fragment.matchAll(/<Component\b([^>]*)>([\s\S]*?)<\/Component>/g),
  ];
  assert.equal(components.length, 3);
  assert.match(fragment, /<DirectoryRef\s+Id="INSTALLDIR">/);
  assert.match(fragment, /<Directory\s+Id="HqBundledResources"\s+Name="_up_">/);
  assert.match(
    fragment,
    /<RemoveFolder\b[^>]*\bDirectory="HqBundledResources"[^>]*\bOn="uninstall"/,
  );
  const sources = [];
  const ids = [];
  for (const component of components) {
    const metadata = attributes(component[1]);
    const body = component[2];
    assert.match(
      metadata.Guid,
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
      "WiX requires an explicit stable GUID when a registry-keypath component also contains a file",
    );
    assert.notEqual(
      metadata.KeyPath,
      "yes",
      "Directory key paths fail ICE38 in user profiles",
    );
    const files = [...body.matchAll(/<File\b[^>]*\/>/g)];
    assert.equal(files.length, 1);
    const file = attributes(files[0][0]);
    assert.equal(file.KeyPath, "no");
    const keys = [...body.matchAll(/<RegistryValue\b[^>]*\/>/g)].map((m) =>
      attributes(m[0]),
    );
    assert.equal(keys.length, 1);
    assert.equal(keys[0].Root, "HKCU");
    assert.equal(keys[0].KeyPath, "yes");
    assert(file.Source.startsWith("$(sys.SOURCEFILEDIR)"));
    const source = resolve(
      dirname(fragmentPath),
      file.Source.slice("$(sys.SOURCEFILEDIR)".length).replaceAll("\\", sep),
    );
    await access(source);
    sources.push(source);
    ids.push(metadata.Id);
  }
  assert.deepEqual(
    sources.sort(),
    base.bundle.resources.map((p) => resolve(nativeRoot, p)).sort(),
  );
  assert.equal(new Set(ids).size, 3);
  const group = fragment.match(
    /<ComponentGroup\s+Id="HqPerUserResources">([\s\S]*?)<\/ComponentGroup>/,
  );
  assert(group);
  assert.deepEqual(
    [...group[1].matchAll(/<ComponentRef\s+Id="([^"]+)"/g)]
      .map((m) => m[1])
      .sort(),
    ids.sort(),
  );
});
