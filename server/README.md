# Huaqing Switch Linux service

The Linux deployment runs this native service and the separately built web interface. Windows/macOS retain the Tauri desktop application. The service shares connection validation, native inference forwarding, and usage accounting directly with `src-tauri/src/hq.rs` and its two modules; it has no GTK, WebKit, or Tauri runtime dependency.

Build with the repository's pinned Rust toolchain:

```sh
cargo build --locked --release --manifest-path server/Cargo.toml
cargo test --locked --manifest-path server/Cargo.toml
cargo clippy --locked --manifest-path server/Cargo.toml --all-targets --all-features -- -D warnings
```

`server/Cargo.lock` is independent of the desktop dependency graph. Serve the output of `npm run build:server` through `HQ_SWITCH_STATIC_DIR`; serving the desktop renderer alone does not provide a server product.

| Environment                      | Meaning                                                                                                                       |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `HQ_SWITCH_WEB_BIND`             | Web/API listener, default `0.0.0.0:8080`. Publish through an HTTPS reverse proxy.                                             |
| `HQ_SWITCH_GATEWAY_BIND`         | Machine inference listener, default `0.0.0.0:17621`. Keep inside the private container network.                               |
| `HQ_SWITCH_GATEWAY_URL`          | Required advertised HTTP(S) origin, e.g. `http://huaqing-switch:17621`. Inference clients must send its exact canonical Host. |
| `HQ_SWITCH_PUBLIC_ORIGIN`        | Required browser HTTPS origin, without an application path. HTTP is accepted only on loopback for local testing.              |
| `HQ_SWITCH_DATA_DIR`             | Required absolute persistent directory. Contains `switch.sqlite`, WAL, and the exclusive process lock.                        |
| `HQ_SWITCH_STATIC_DIR`           | Required absolute directory containing the built web `index.html` and assets.                                                 |
| `HQ_SWITCH_ADMIN_TOKEN_FILE`     | Required private administrative token file.                                                                                   |
| `HQ_SWITCH_INFERENCE_TOKEN_FILE` | Required separate private machine inference token file.                                                                       |

New administrative credentials use `sk-` followed by 64 hexadecimal characters (32 random bytes). Existing unprefixed 64-hex admin credentials remain supported. Machine inference credentials still contain exactly 64 hexadecimal characters and reject the `sk-` prefix. Authentication compares the complete credential, including `sk-`; initialization preserves existing credentials and never rotates them on rerun. Admin and inference credentials must use independent random material; reusing the same 64-hex payload across the two roles is rejected even when the admin adds `sk-`. Both must be regular files owned by the service user, mode `0600`, without symlinks. Token values are never accepted as environment-variable values or baked into an image. Rootless Docker UID 0 can read host-ubuntu-owned `0600` bind-mounted credentials because that UID maps to the unprivileged host user. Keep the data directory persistent and run one service process per data volume; the service enforces an OS exclusive lock before recovering unfinished usage rows.

The web API exposes `GET/POST/DELETE /api/session` and `POST /api/commands/:name`. Browser mutations require the configured exact `Origin` plus `X-HQ-CSRF: 1`; login sets a host-only `HttpOnly; Secure; SameSite=Strict` cookie with an eight-hour fixed lifetime. Sessions are memory-only, bounded to 256, and invalidated by restart. Login attempts are bounded per direct peer and globally; forwarded IP headers are not trusted. CLI operators may instead send the admin Bearer token without Origin. Commands are limited to the seven HQ connection/status/usage operations; server retry only reads status and never restarts listeners through HTTP. Desktop retry behavior is unchanged.

Inference uses only the separate inference credential, rejects browser Origin headers, and checks the advertised Host. Admin credentials cannot perform inference; inference credentials cannot manage connections. The inference service preserves the desktop request limits, cancellation, upstream credential injection, and JSON/SSE usage accounting. SQLite holds provider credentials and metadata, so protect volume backups as credentials. Usage rows never store prompts, responses, provider URLs, or API keys.

`huaqing-switch-server healthcheck` performs an unauthenticated local `/healthz` probe without curl or credential files. Readiness requires the inference listener and readable usage storage. SIGTERM/SIGINT drain accepted inference work for up to 310 seconds and allow another five seconds for final writes; configure the container stop grace period to at least 330 seconds. Web requests have a 15-second bound and 64-KiB body limit. The front end receives CSP, no-sniff, no-store, and frame-denial headers; undefined API paths never fall back to HTML.
