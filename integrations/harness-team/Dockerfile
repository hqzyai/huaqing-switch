# These are the deployed, independently fingerprinted Team baselines.
ARG GATEWAY_BASE=huaqing-team-gateway:0.2.1-rc.2-model-setup-20261026
ARG WORKER_BASE=huaqing-team-worker:0.2.1-rc.2-bailian-20261026
FROM ${GATEWAY_BASE} AS gateway
ARG SOURCE_COMMIT
LABEL org.opencontainers.image.title="HQHarness Team with Huaqing Switch" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}"
RUN echo '47bfac4a61ef31bd8288ef7f917c17c6f935e30a502f629bff4b3f34c97d363c  /opt/dsh/node_modules/@deepseek-ai/dsh-host-team-server/lib/index.js' | sha256sum -c - \
    && echo 'b7fe12bef54d91fd850e7035e7cc887a0722021aeaa0e2dcd0d3aa6dbd031f7d  /opt/dsh/node_modules/@deepseek-ai/dsh-host-team-runtime/lib/index.js' | sha256sum -c -
COPY addon/ /opt/dsh/node_modules/@hqzyai/huaqing-switch-team/
COPY artifacts/team-server-index.js /opt/dsh/node_modules/@deepseek-ai/dsh-host-team-server/lib/index.js
COPY artifacts/team-runtime-index.js /opt/dsh/node_modules/@deepseek-ai/dsh-host-team-runtime/lib/index.js
COPY patch-manifest.json /opt/dsh/huaqing-switch-patch-manifest.json
RUN node --check /opt/dsh/node_modules/@deepseek-ai/dsh-host-team-server/lib/index.js \
    && node --check /opt/dsh/node_modules/@deepseek-ai/dsh-host-team-runtime/lib/index.js \
    && node --check /opt/dsh/node_modules/@hqzyai/huaqing-switch-team/index.mjs

FROM ${WORKER_BASE} AS worker
ARG SOURCE_COMMIT
LABEL org.opencontainers.image.title="HQHarness Worker with Huaqing Switch" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}"
RUN echo 'b7fe12bef54d91fd850e7035e7cc887a0722021aeaa0e2dcd0d3aa6dbd031f7d  /opt/dsh/node_modules/@deepseek-ai/dsh-host-team-runtime/lib/index.js' | sha256sum -c -
COPY addon/ /opt/dsh/node_modules/@hqzyai/huaqing-switch-team/
COPY artifacts/team-runtime-index.js /opt/dsh/node_modules/@deepseek-ai/dsh-host-team-runtime/lib/index.js
COPY patch-manifest.json /opt/dsh/huaqing-switch-patch-manifest.json
RUN node --check /opt/dsh/node_modules/@deepseek-ai/dsh-host-team-runtime/lib/index.js \
    && node --check /opt/dsh/node_modules/@hqzyai/huaqing-switch-team/index.mjs
