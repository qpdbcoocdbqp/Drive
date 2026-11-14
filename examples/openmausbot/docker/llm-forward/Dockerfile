# Loopback forwarder (socat). Built by compose.build.yaml as omb-llm-forward:local.
# alpine:3.22 (index digest pulled on 2026-11-14)
FROM alpine:3.22@sha256:49340b346798875b5592e5d420241f2f9cfba5f47e62ebb1446a1b34734e898f
RUN apk add --no-cache socat
USER 65001:65001
ENTRYPOINT ["socat"]
