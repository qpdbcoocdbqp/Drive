# Connection monitor: kernel conntrack events + a logging dnsmasq forwarder (see monitor.sh). No packet parser.
# alpine:3.22 (index digest pulled on 2026-11-14)
FROM alpine:3.22@sha256:49340b346798875b5592e5d420241f2f9cfba5f47e62ebb1446a1b34734e898f
RUN apk add --no-cache conntrack-tools dnsmasq iptables ip6tables iproute2
COPY monitor.sh /usr/local/bin/monitor.sh
# A Windows checkout may convert line endings to CRLF, which sh cannot read
RUN sed -i 's/\r$//' /usr/local/bin/monitor.sh && chmod 0755 /usr/local/bin/monitor.sh
ENTRYPOINT ["/usr/local/bin/monitor.sh"]
