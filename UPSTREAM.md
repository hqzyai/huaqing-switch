# Huaqing Switch upstream provenance

This independent workspace is named **Huaqing Model Connection**. Its product name is **Huaqing Switch**.

- Upstream: https://github.com/farion1231/cc-switch
- Source commit: `cb90a2440d1a771d67f46b65b2e0f2ea5106ce85`
- Source: GitHub's official codeload archive; its Git archive commit header was verified before import.
- Local baseline: `e74bfbd` is an import commit of the archive, not the original upstream commit or complete upstream Git history.
- Git remotes: `upstream` points to the original repository; `origin` points to the user-owned fork [hqzyai/huaqing-switch](https://github.com/hqzyai/huaqing-switch). Product changes are published on `dev`, preserving the fork’s existing history. The initial archive import remains a local provenance record.
- Fork baseline for the first `dev` publication: `07265bc97035a51db7d4ce58890ea3c3e66076cc`, which includes three later upstream Kimi commits. These commits are retained; the product patch is applied on top. Current upstream README snapshots are in `docs/upstream-readmes/`.
- License: preserve the original [MIT LICENSE](./LICENSE), including its copyright notice.

Current status: the display name is Huaqing Switch. The shipping entry mounts an HQ-only workspace; legacy clients, routing, aggregation and auxiliary navigation are hidden, and legacy startup is bypassed. Authenticated loopback inference, HQHarness discovery, native Pi protocol adapters and client model-settings suppression are implemented. Real native Switch and real Harness runtime integration has passed against a deterministic local API fixture. See [the integration record](docs/huaqing-harness-integration.md). Local unsigned macOS packages have been built. Paid-provider and Windows/Linux native acceptance have not been run on this Mac.

The target scope is Windows, Linux and macOS. HQHarness is the sole visible client. Existing installed CC Switch data has not been modified. The user-authorized companion changes are in the separate `HQHarness` repository; its pre-existing working changes have been preserved. Stable package, application and storage identifiers remain unchanged to preserve installed configurations.
