import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import {
  mkdir,
  readFile,
  writeFile,
  copyFile,
  readdir,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const brand = JSON.parse(
  await readFile(path.join(root, "brand.config.json"), "utf8"),
);
const tauri = JSON.parse(
  await readFile(path.join(root, "src-tauri/tauri.conf.json"), "utf8"),
);
const pkg = JSON.parse(await readFile(path.join(root, "package.json"), "utf8"));
if (brand.clientMode !== "hq-only") {
  throw new Error(
    "This product entry requires the hq-only native startup policy",
  );
}
if (
  tauri.productName !== brand.productName ||
  tauri.identifier !== brand.identifier ||
  tauri.version !== brand.version ||
  pkg.name !== brand.packageName ||
  pkg.version !== brand.version
) {
  throw new Error(
    "Product identity differs between brand.config.json and package manifests",
  );
}
if (
  !brand.updatesEnabled &&
  (tauri.plugins.updater || tauri.bundle.createUpdaterArtifacts)
) {
  throw new Error("Unconfigured builds must not contain an upstream updater");
}
const macPlist = await readFile(
  path.join(root, "src-tauri/Info.plist"),
  "utf8",
);
if (
  !macPlist.includes(`<string>${brand.deepLinkScheme}</string>`) ||
  !macPlist.includes(`<string>${brand.productName} Deep Link</string>`) ||
  JSON.stringify(tauri.plugins["deep-link"].desktop.schemes) !==
    JSON.stringify([brand.deepLinkScheme])
) {
  throw new Error(
    "Native deep-link registrations differ from the product identity",
  );
}
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
for (const entry of ["src/index.html", "src/tray-panel.html"]) {
  const html = await readFile(path.join(root, entry), "utf8");
  const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
  const digest = createHash("sha256")
    .update(script ?? "")
    .digest("base64");
  if (!script || !tauri.app.security.csp.includes(`'sha256-${digest}'`)) {
    throw new Error(`CSP theme bootstrap hash is stale for ${entry}`);
  }
}
const profile = JSON.parse(
  await readFile(
    path.join(root, "shadcn-ui-unify/assets/neutral-theme.json"),
    "utf8",
  ),
);
const logo = await readFile(
  path.join(root, "shadcn-ui-unify/assets/branding/hqzyai-logomark.svg"),
);
if (hash(logo) !== profile.branding.consoleLogo.sha256)
  throw new Error("HQZY logo hash mismatch");

const output = path.join(root, "src/assets/generated");
const licenses = path.join(root, "src/public/licenses");
await mkdir(output, { recursive: true });
await mkdir(licenses, { recursive: true });
await mkdir(path.join(root, "src/public"), { recursive: true });
await writeFile(path.join(root, "src/public/hqzyai-logomark.svg"), logo);
await mkdir(path.join(root, "src/assets/branding"), { recursive: true });
await writeFile(
  path.join(root, "src/assets/branding/hqzyai-logomark.svg"),
  logo,
);

const fonts = [
  ["geist", "Geist", "5.2.6"],
  ["geist-mono", "Geist Mono", "5.2.6"],
  ["noto-sans-sc", "Noto Sans SC", "5.3.1"],
];
let css = "/* Generated from pinned Fontsource packages. Do not edit. */\n";
const files = [];
for (const [name, family, version] of fonts) {
  const source = path.join(root, "node_modules/@fontsource-variable", name);
  const metadata = JSON.parse(
    await readFile(path.join(source, "package.json"), "utf8"),
  );
  if (metadata.version !== version)
    throw new Error(`Unexpected ${name} version: ${metadata.version}`);
  const destination = path.join(output, name);
  await mkdir(destination, { recursive: true });
  for (const filename of await readdir(path.join(source, "files"))) {
    if (!filename.endsWith("-wght-normal.woff2")) continue;
    const bytes = await readFile(path.join(source, "files", filename));
    await writeFile(path.join(destination, filename), bytes);
    files.push({
      font: family,
      version,
      file: `${name}/${filename}`,
      sha256: hash(bytes),
    });
  }
  // Retain the full upstream Unicode ranges; alias the family to the design contract.
  css +=
    (await readFile(path.join(source, "index.css"), "utf8"))
      .replaceAll(`${family} Variable`, family)
      .replaceAll("./files/", `./${name}/`) + "\n";
  await copyFile(
    path.join(source, "LICENSE"),
    path.join(licenses, `${name}-OFL.txt`),
  );
}
await writeFile(path.join(output, "fonts.css"), css);
await copyFile(
  path.join(root, "node_modules/lucide-react/LICENSE"),
  path.join(licenses, "lucide-react-LICENSE.txt"),
);
await copyFile(
  path.join(root, "LICENSE"),
  path.join(licenses, "cc-switch-MIT.txt"),
);
await writeFile(
  path.join(licenses, "assets.json"),
  JSON.stringify(
    {
      theme: profile.id,
      iconProfile: profile.icons.profile,
      lucideVersion: JSON.parse(
        await readFile(
          path.join(root, "node_modules/lucide-react/package.json"),
          "utf8",
        ),
      ).version,
      brand: {
        profile: profile.branding.profile,
        sha256: hash(logo),
        rights: profile.branding.rights,
      },
      files,
    },
    null,
    2,
  ) + "\n",
);

// A square application icon frame preserves the source artwork's 793:568 ratio.
const square = `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024"><svg x="112" y="225.5" width="800" height="573" viewBox="0 0 793 568">${logo.toString().replace(/<\?xml[^>]*>|<\/?svg[^>]*>/g, "")}</svg></svg>`;
const iconSource = path.join(output, "app-icon.svg");
await writeFile(iconSource, square);
execFileSync(
  process.execPath,
  [
    path.join(root, "node_modules/@tauri-apps/cli/tauri.js"),
    "icon",
    iconSource,
    "--output",
    path.join(root, "src-tauri/icons/hq"),
  ],
  { cwd: root, stdio: "pipe" },
);
console.log(
  `Prepared HQ branding and ${files.length} font subsets; no runtime font downloads.`,
);
