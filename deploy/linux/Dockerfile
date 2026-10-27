# syntax=docker/dockerfile:1
ARG NODE_IMAGE=node:24.19.0-bookworm-slim@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df
ARG RUST_IMAGE=rust:1.95.0-bookworm@sha256:6258907abe69656e41cd992e0b705cdcfabcbbe3db374f92ed2d47121282d4a1
ARG RUNTIME_IMAGE=debian:bookworm-slim@sha256:ec31d744f83f4a73447ba045aaab9409997efe5cc706eca4890c1be36cc1606a
FROM ${NODE_IMAGE} AS web
ENV CI=true
WORKDIR /source
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN corepack enable && corepack prepare pnpm@10.12.3 --activate
RUN --mount=type=cache,target=/pnpm/store pnpm install --frozen-lockfile --ignore-scripts --store-dir /pnpm/store
COPY . .
RUN pnpm run typecheck && pnpm run build:server

FROM ${RUST_IMAGE} AS backend
WORKDIR /source
COPY server ./server
COPY brand.config.json ./
COPY src-tauri/src/hq.rs ./src-tauri/src/hq.rs
COPY src-tauri/src/hq ./src-tauri/src/hq
ENV CARGO_INCREMENTAL=0
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo build --locked --release --manifest-path server/Cargo.toml

FROM ${RUNTIME_IMAGE}
ARG SOURCE_COMMIT
LABEL org.opencontainers.image.title="Huaqing Switch Server" \
      org.opencontainers.image.source="https://github.com/hqzyai/huaqing-switch" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}"
WORKDIR /app
COPY --from=backend /source/server/target/release/huaqing-switch-server /app/huaqing-switch-server
COPY --from=web /source/dist-server /app/dist-server
COPY LICENSE /app/LICENSE
EXPOSE 8080 8081 17621
ENTRYPOINT ["/app/huaqing-switch-server"]
