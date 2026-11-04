import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const template = await readFile(
  new URL("../src-tauri/wix/per-user-main.wxs", import.meta.url),
  "utf8",
);

test("new installs default to LocalAppData\\huaqing\\switch", () => {
  assert.match(
    template,
    /<Directory Id="LocalAppDataFolder">\s*<Directory Id="HuaqingLocalAppDataFolder" Name="huaqing">\s*<Directory Id="INSTALLDIR" Name="switch"\s*\/>/,
  );
});

test("upgrades can reuse the existing per-user installation directory", () => {
  assert.match(
    template,
    /<Property Id="INSTALLDIR">[\s\S]*?<RegistrySearch\b[^>]*\bRoot="HKCU"[^>]*\bName="InstallDir"[^>]*\/>/,
  );
});

test("uninstall never removes files from the shared per-user huaqing directory", () => {
  const cleanup = [
    ...template.matchAll(/<RemoveFile\b[^>]*\/\s*>/g),
  ];
  assert(cleanup.length > 0, "Expected application uninstall cleanup entries");
  for (const [entry] of cleanup) {
    assert.doesNotMatch(
      entry,
      /\bDirectory\s*=\s*["']HuaqingLocalAppDataFolder["']/,
      "The shared huaqing directory can contain other applications",
    );
  }
  assert.match(
    template,
    /<RemoveFolder\b[^>]*\bDirectory="HuaqingLocalAppDataFolder"[^>]*\bOn="uninstall"[^>]*\/\s*>/,
    "MSI requires cleanup of empty profile directories; RemoveFolder preserves all files",
  );
});
