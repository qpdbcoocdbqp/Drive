#!/bin/sh
# Passive connection monitor for one network namespace. No packet contents are captured or parsed:
# - conntrack (the kernel's netfilter connection tracking) reports every new connection: protocol, addresses, ports.
# - dnsmasq (NETMON_DNS=on) is this namespace's DNS forwarder and logs every query and answer, so the report can name IPs.
#
# Security design:
# - The only untrusted input parsed here is DNS, by dnsmasq, a widely deployed forwarder (OpenWrt, Pi-hole, libvirt).
# - dnsmasq binds 127.0.0.1:53 as root and then drops to the dnsmasq user. iptables lets that user send only DNS
#   replies on loopback and DNS queries to the upstream resolver: it cannot reach omb's 127.0.0.1:8799 (headless
#   OpenMausBot treats loopback requests as the owner), Caddy's admin API, or anything else.
# - conntrack reads connection events from the kernel over netlink; it never sees packet payloads.
# - Every process runs in a supervised loop and this script never exits: the containers that join this namespace lose
#   their network when its owner stops.
set -eu

NAME="${NETMON_NAME:-omb}"
DNS="${NETMON_DNS:-on}"
UPSTREAM="${NETMON_DNS_UPSTREAM:-192.168.65.7}"
KEEP_DAYS="${NETMON_RETENTION_DAYS:-14}"
mkdir -p /logs

# This namespace's own IPs, used by the report to tell outbound from inbound. Accumulated rather than overwritten:
# IPs change when the Docker network is recreated, and older records must still be classifiable.
{ cat "/logs/${NAME}.ips" 2>/dev/null || true; ip -o addr show | awk '{print $4}' | cut -d/ -f1; } \
  | sort -u > "/logs/${NAME}.ips.new" && mv "/logs/${NAME}.ips.new" "/logs/${NAME}.ips"
echo "[netmon] ${NAME}: own addresses: $(tr '\n' ' ' < "/logs/${NAME}.ips")"

# Appends each input line to /logs/<prefix>-YYYYMMDD.log (UTC), prefixed with a Unix timestamp and a tab.
stamp() {
  awk -v base="/logs/$1" '{
    f = base "-" strftime("%Y%m%d", systime()) ".log"
    if (f != cur) { if (cur != "") close(cur); cur = f }
    print systime() "\t" $0 >> f
    fflush(f)
  }'
}

if [ "$DNS" = "on" ]; then
  DNS_UID="$(id -u dnsmasq)"
  # Rules for the dnsmasq user, in their own chain so a restart replaces them instead of stacking duplicates.
  iptables -N NETMON_DNS 2>/dev/null || iptables -F NETMON_DNS
  iptables -A NETMON_DNS -o lo -p udp --sport 53 -m conntrack --ctstate ESTABLISHED -j RETURN
  iptables -A NETMON_DNS -o lo -p tcp --sport 53 -m conntrack --ctstate ESTABLISHED -j RETURN
  iptables -A NETMON_DNS -d "$UPSTREAM" -p udp --dport 53 -j RETURN
  iptables -A NETMON_DNS -d "$UPSTREAM" -p tcp --dport 53 -j RETURN
  iptables -A NETMON_DNS -j DROP
  iptables -C OUTPUT -m owner --uid-owner "$DNS_UID" -j NETMON_DNS 2>/dev/null \
    || iptables -I OUTPUT -m owner --uid-owner "$DNS_UID" -j NETMON_DNS
  ip6tables -C OUTPUT -m owner --uid-owner "$DNS_UID" -j DROP 2>/dev/null \
    || ip6tables -I OUTPUT -m owner --uid-owner "$DNS_UID" -j DROP
  echo "[netmon] ${NAME}: dnsmasq on 127.0.0.1:53 -> ${UPSTREAM} (uid ${DNS_UID} restricted to DNS)"

  (
    while true; do
      dnsmasq --keep-in-foreground --log-facility=- --log-queries=extra \
        --no-resolv --no-hosts --no-poll --server="$UPSTREAM" \
        --listen-address=127.0.0.1 --bind-interfaces --port=53 \
        --user=dnsmasq --group=dnsmasq --pid-file= --cache-size=1000 2>&1 | stamp "${NAME}-dns"
      echo "[netmon] ${NAME}: dnsmasq exited; restarting" >&2
      sleep 2
    done
  ) &
fi

(
  while true; do
    conntrack -E -e NEW 2>&1 | stamp "${NAME}-conn"
    echo "[netmon] ${NAME}: conntrack exited; restarting" >&2
    sleep 2
  done
) &

while true; do
  find /logs -name "${NAME}-*.log" -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
  sleep 3600
done
