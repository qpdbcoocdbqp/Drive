# OpenMausBot server image (replaces upstream deploy/local/Dockerfile). Built by compose.build.yaml as openmausbot-local:latest.
#
# Upgrade: change the BASE_IMAGE tag + digest and the ENGINES versions, rebuild, then re-run Trivy.

# ghcr.io/milind-soni/openmausbot:v0.1.158, the digest scanned on 2026-11-14 (the "stable" tag in the upstream docs does not exist)
ARG BASE_IMAGE=ghcr.io/milind-soni/openmausbot:v0.1.158@sha256:84c101f3c87d142f644f789b3b21f40a18e06e7460a25b25573389477faf9a00
FROM ${BASE_IMAGE}

USER root

# The npm 11.19.1 bundled in the base image carries 19 known vulnerabilities (undici, brace-expansion, ip-address, ...)
RUN npm install -g npm@11.21.2 && npm --version

# Engine CLIs pinned by version. npm 11.20+ blocks install scripts by default; allow it only for claude-code (its postinstall downloads a native binary).
# Grok is not installed: upstream runs xAI's install script through curl (version pinned, hash not verified).
ARG ENGINES="@anthropic-ai/claude-code@2.1.325 @openai/codex@0.169.0"
RUN if [ -n "$ENGINES" ]; then npm install -g --allow-scripts=@anthropic-ai/claude-code $ENGINES; fi \
 && claude --version && codex --version

# Usage analytics (PostHog) off by default: upstream only checks browser localStorage and treats "never set" as on, with no server-side switch.
# Changed to "on only when explicitly enabled in Settings (localStorage=0)". If the pattern no longer matches, the build fails so a version bump cannot silently undo this.
RUN cd /app \
 && sed -i -E 's/try\{return localStorage\.getItem\(([A-Za-z0-9_$]+)\)!=="1"\}catch\{return!0\}/try{return localStorage.getItem(\1)==="0"}catch{return!1}/' dist/assets/*.js \
 && test "$(grep -lE 'localStorage\.getItem\([A-Za-z0-9_$]+\)==="0"\}catch\{return!1\}' dist/assets/*.js | wc -l)" = 1 \
 && ! grep -qE 'localStorage\.getItem\([A-Za-z0-9_$]+\)!=="1"\}catch\{return!0\}' dist/assets/*.js \
 && echo "analytics default: off"

USER maus
