import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const template = await readFile(
  new URL("../src-tauri/wix/per-user-main.wxs", import.meta.url),
  "utf8",
);

test("new installs default to LocalAppData\\HuaqingSwitch", () => {
  assert.match(
    template,
    /<Directory Id="LocalAppDataFolder">\s*<Directory Id="INSTALLDIR" Name="HuaqingSwitch"\s*\/>/,
  );
});

test("only maintenance reuses the existing per-user installation directory", () => {
  assert.match(
    template,
    /<Property Id="PREVIOUSINSTALLDIR"[^>]*>\s*<RegistrySearch\b[^>]*\bRoot="HKCU"[^>]*\bName="InstallDir"[^>]*\/>/,
  );
  assert.match(
    template,
    /<SetProperty Id="INSTALLDIR" Value="\[PREVIOUSINSTALLDIR\]"[^>]*>\s*Installed AND PREVIOUSINSTALLDIR AND NOT INSTALLDIR\s*<\/SetProperty>/,
  );
});

test("uninstall cleanup is scoped to the application directory", () => {
  const cleanup = [
    ...template.matchAll(/<RemoveFile\b[^>]*\/\s*>/g),
  ];
  assert(cleanup.length > 0, "Expected application uninstall cleanup entries");
  for (const [entry] of template.matchAll(/<Remove(?:File|Folder)\b[^>]*\/\s*>/g)) {
    assert.doesNotMatch(
      entry,
      /\b(?:Directory|Id)\s*=\s*["']LocalAppDataFolder["']/,
      "The local application data directory belongs to other applications too",
    );
  }
  assert.match(
    template,
    /<RemoveFolder\b[^>]*\bId="INSTALLDIR"[^>]*\bOn="uninstall"[^>]*\/\s*>/,
    "RemoveFolder cleans up the installation directory when empty",
  );
});
