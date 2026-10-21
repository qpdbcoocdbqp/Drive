# Harness² test image: Debian 12 + Python 3.12 + uv + Node 20 + pandoc/poppler (+ optional gcloud).
# Build context: this directory.  See ../README.md section 1 for usage.

FROM node:20-bookworm-slim AS node
FROM ghcr.io/astral-sh/uv:0.8.15 AS uv

FROM python:3.12-slim-bookworm

ARG HARNESS2_REPO=https://github.com/google-research/harness2.git
ARG HARNESS2_REF=1cfe254e8d25b15f37048c3730134686abe60335
# render: JobBench/WorkBuddy rendering deps; add ",bridge" for the Codex LiteLLM bridge.
ARG HARNESS2_EXTRAS=render
# 1 = install Google Cloud CLI (needed for Vertex AI models); 0 = local models only.
ARG INSTALL_GCLOUD=1
ARG UID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git curl unzip ca-certificates gnupg procps \
      pandoc poppler-utils \
 && if [ "$INSTALL_GCLOUD" = "1" ]; then \
      curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
   && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
        > /etc/apt/sources.list.d/google-cloud-sdk.list \
   && apt-get update && apt-get install -y --no-install-recommends google-cloud-cli; \
    fi \
 && rm -rf /var/lib/apt/lists/*

# Node.js 20 (+ npm/npx) and uv from their official images.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY --from=uv /uv /uvx /usr/local/bin/

RUN useradd -m -u "$UID" -s /bin/bash harness \
 && mkdir -p /opt/harness2 /opt/venv && chown harness:harness /opt/harness2 /opt/venv
USER harness
WORKDIR /opt/harness2

# The venv lives outside the source tree and installs harness2 in editable mode, so a host
# checkout bind-mounted over /opt/harness2 (compose.yaml: ./module) is what actually runs.
RUN git clone "$HARNESS2_REPO" . \
 && git checkout --detach "$HARNESS2_REF" \
 && uv venv --python /usr/local/bin/python3.12 /opt/venv \
 && uv pip install --python /opt/venv/bin/python -e ".[${HARNESS2_EXTRAS}]" \
 && mkdir -p runs /home/harness/.config/opencode /home/harness/.config/gcloud

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# dependencies/: benchmark checkouts + data from harness2-setup; runs/: results.
VOLUME ["/opt/harness2/dependencies", "/opt/harness2/runs"]

# Smoke test without model credentials.
CMD ["harness2-demo", "--output", "runs/demo", "--k", "2"]
