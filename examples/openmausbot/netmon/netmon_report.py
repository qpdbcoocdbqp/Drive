"""Summarize the connection records written by netmon (conntrack + dnsmasq): where OpenMausBot connects to, and who
connects in.

Usage:
    python netmon_report.py                 # all records
    python netmon_report.py --since 2       # only the last 2 hours
    python netmon_report.py --only-flagged  # only the author's own domains / telemetry
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Destinations that deserve attention (regex matched against the hostname)
FLAGS: list[tuple[str, re.Pattern[str]]] = [
    ("AUTHOR", re.compile(r"(^|\.)(openmausbot\.com|mausbot\.(com|page))$|^omb-[a-z0-9-]+\.fly\.dev$|milindsoni201\.workers\.dev$")),
    ("TELEMETRY", re.compile(r"(^|\.)(posthog\.com|sentry\.io|statsig(api)?\.(com|net)|segment\.io|datadoghq\.com)$")),
]

# DNS names come from the network: strip control characters and bidi override characters before printing to the
# terminal (prevents ANSI escape sequence injection)
UNSAFE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f‎‏‪-‮⁦-⁩]")

# conntrack -E: "[NEW] tcp      6 120 SYN_SENT src=A dst=B sport=1 dport=2 [UNREPLIED] src=B dst=A ..."
CONN = re.compile(r"\[NEW\]\s+(?:ipv[46]\s+\d+\s+)?(\w+)\s+\d+\s+(.*)")
# dnsmasq --log-queries=extra: "<serial> <client>/<port> query[A] name from client" / "... reply name is 1.2.3.4"
DNS_QUERY = re.compile(r"\b(\d+) \S+ query\[(\w+)\] (\S+) from ")
DNS_ANSWER = re.compile(r"\b(\d+) \S+ (?:reply|cached|config) (\S+) is (\S+)\s*$")


def flag_of(name: str) -> str:
    return next((label for label, pattern in FLAGS if pattern.search(name)), "")


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def is_loopback(value: str) -> bool:
    return is_ip(value) and ipaddress.ip_address(value).is_loopback


@dataclass
class Flow:
    count: int = 0
    first: float = float("inf")
    last: float = 0.0
    ips: set[str] = field(default_factory=set)

    def see(self, ts: float) -> None:
        self.count += 1
        self.first, self.last = min(self.first, ts), max(self.last, ts)


def lines(log_dir: Path, pattern: str, since: float):
    for path in sorted(log_dir.glob(pattern)):
        with path.open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                stamp, _, text = UNSAFE.sub("?", raw.rstrip("\n")).partition("\t")
                try:
                    ts = float(stamp)
                except ValueError:
                    continue
                if ts >= since:
                    yield ts, text


def original_direction(rest: str) -> dict[str, str]:
    """Key/value pairs of the original direction of a conntrack event (before the reply tuple's own src=)."""
    head = rest.split(" src=", 2)
    original = head[0] + (" src=" + head[1] if len(head) > 1 else "")
    return dict(re.findall(r"(\w+)=(\S+)", original))


def report(log_dir: Path, name: str, since: float, only_flagged: bool) -> None:
    ips_file = log_dir / f"{name}.ips"
    if not ips_file.exists():
        print(f"== {name}: no records ({ips_file} does not exist)")
        return
    own = {line.strip() for line in ips_file.read_text().splitlines() if line.strip()}

    # DNS: map each answer IP back to the name that was queried (by serial, so CNAME chains keep the original name)
    names: dict[str, str] = {}
    queries: dict[str, Flow] = defaultdict(Flow)
    asked: dict[str, str] = {}
    for ts, text in lines(log_dir, f"{name}-dns-*.log", since):
        if m := DNS_QUERY.search(text):
            serial, _qtype, qname = m.groups()
            asked[serial] = qname
            queries[qname].see(ts)
        elif m := DNS_ANSWER.search(text):
            serial, rname, value = m.groups()
            if is_ip(value):
                names[value] = asked.get(serial, rname)

    outbound: dict[tuple[str, str], Flow] = defaultdict(Flow)
    inbound: dict[tuple[str, str], Flow] = defaultdict(Flow)
    events = 0
    for ts, text in lines(log_dir, f"{name}-conn-*.log", since):
        m = CONN.search(text)
        if not m:
            continue
        proto, rest = m.groups()
        kv = original_direction(rest)
        src, dst = kv.get("src", ""), kv.get("dst", "")
        if not src or not dst or is_loopback(src) or is_loopback(dst):
            continue
        events += 1
        port = f"{kv.get('dport', kv.get('type', '-'))}/{proto}"
        if dst in own and src not in own:
            inbound[(src, port)].see(ts)
        else:
            host = names.get(dst, dst)
            flow = outbound[(host, port)]
            flow.see(ts)
            flow.ips.add(dst)

    print(f"\n== {name} (own addresses: {', '.join(sorted(own))}; {events} connections, {sum(q.count for q in queries.values())} DNS queries)")

    print("\n-- Outbound connections (destination host port/protocol)")
    printed = 0
    for (host, port), flow in sorted(outbound.items(), key=lambda kv: (not flag_of(kv[0][0]), -kv[1].count)):
        tag = flag_of(host)
        if only_flagged and not tag:
            continue
        printed += 1
        ips = ",".join(sorted(ip for ip in flow.ips if ip != host)[:3])
        print(f"  {tag:9} {host:48} {port:10} conns {flow.count:4}  {fmt(flow.first)} ~ {fmt(flow.last)}  {ips}")
    if not printed:
        print("  (none)")

    if not only_flagged:
        print("\n-- DNS queries")
        for qname, flow in sorted(queries.items(), key=lambda kv: (not flag_of(kv[0]), -kv[1].count)):
            print(f"  {flag_of(qname):9} {qname:48} count {flow.count:4}  last {fmt(flow.last)}")
        if not queries:
            print("  (none)")

    print("\n-- Inbound connections (source IP -> local port/protocol)")
    for (src, port), flow in sorted(inbound.items(), key=lambda kv: -kv[1].count):
        print(f"  {src + ' -> ' + port:40} conns {flow.count:4}  {fmt(flow.first)} ~ {fmt(flow.last)}")
    if not inbound:
        print("  (none)")


def fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S") if ts not in (0.0, float("inf")) else "-"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log-dir", type=Path, default=Path(__file__).resolve().parent / "logs")
    parser.add_argument("--since", type=float, help="only the last N hours")
    parser.add_argument("--name", action="append", help="omb / edge (default: both)")
    parser.add_argument("--only-flagged", action="store_true", help="only the author's own domains and telemetry endpoints")
    args = parser.parse_args()
    since = time.time() - args.since * 3600 if args.since else 0.0
    for name in args.name or ["omb", "edge"]:
        report(args.log_dir, name, since, args.only_flagged)


if __name__ == "__main__":
    main()
