import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const template = await readFile(
  new URL("../src-tauri/wix/per-user-main.wxs", import.meta.url),
  "utf8",
);

test("uninstall never removes files from the shared per-user Programs directory", () => {
  const cleanup = [
    ...template.matchAll(/<RemoveFile\b[^>]*\/\s*>/g),
  ];
  assert(cleanup.length > 0, "Expected application uninstall cleanup entries");
  for (const [entry] of cleanup) {
    assert.doesNotMatch(
      entry,
      /\bDirectory\s*=\s*["']TauriLocalAppDataPrograms["']/,
      "The shared Programs directory and its files belong to other applications too",
    );
  }
  assert.match(
    template,
    /<RemoveFolder\b[^>]*\bDirectory="TauriLocalAppDataPrograms"[^>]*\/\s*>/,
    "MSI requires cleanup of empty profile directories; RemoveFolder preserves all files",
  );
});
