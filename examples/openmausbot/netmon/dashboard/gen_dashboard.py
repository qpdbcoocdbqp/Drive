"""Generates grafana/dashboards/netmon.json (Grafana loads it read-only). Edit this file, not the JSON:

    python gen_dashboard.py

Inbound vs outbound uses each namespace's own addresses from ../logs/<name>.ips, the same rule as netmon_report.py.
monitor.sh adds new addresses to that file when the Docker network is recreated: run this again afterwards.
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_DIR = HERE.parent / "logs"
DS = {"type": "loki", "uid": "netmon-loki"}
LOOP = r'"127\\..*|::1"'  # LogQL label filters are fully anchored

# conntrack: "[NEW] tcp      6 120 SYN_SENT src=A dst=B sport=1 dport=2 [UNREPLIED] src=B dst=A ..."
# The first src=/dst= pair is the original direction. ICMP has no ports: show its type instead, like the report.
CONN = (
    r"| regexp `\[NEW\]\s+(?:ipv[46]\s+\d+\s+)?(?P<proto>[a-z0-9]+)\s+\d+\s.*?src=(?P<src>\S+) dst=(?P<dst>\S+)"
    r"(?: sport=\d+ dport=(?P<dport>\d+)| type=(?P<icmptype>\d+))?`"
    # A line the regexp does not match keeps empty labels and would still be counted: require the fields
    f' | src != "" | dst != "" | src !~ {LOOP} | dst !~ {LOOP}'
    ' | label_format dport="{{ or .dport .icmptype }}"'
)


def own(name: str) -> str:
    """LogQL regex (inside a double-quoted string) matching the namespace's own addresses."""
    path = LOG_DIR / f"{name}.ips"
    ips = sorted({line.strip() for line in path.read_text().splitlines() if line.strip()}) if path.exists() else []
    if not ips:
        print(f"warning: {path} not found; every {name} connection counts as outbound", file=sys.stderr)
        return ""  # matches only an empty value, and src / dst are never empty here
    return "|".join(re.escape(ip) for ip in ips).replace("\\", "\\\\")


OWN_OMB, OWN_EDGE = own("omb"), own("edge")
# netmon_report.py: inbound when dst is one of ours and src is not; everything else is outbound
OMB_CONN = '{job="netmon", name="omb", kind="conn"} ' + CONN + f' | dst !~ "{OWN_OMB}" or src =~ "{OWN_OMB}"'
EDGE_CONN = '{job="netmon", name="edge", kind="conn"} ' + CONN + f' | dst =~ "{OWN_EDGE}" | src !~ "{OWN_EDGE}"'
DNS_Q = (
    r'{job="netmon", kind="dns"} |= "query[" | regexp `\d+ \S+ query\[(?P<qtype>\w+)\] (?P<qname>\S+) from `'
    ' | qname != ""'
)
DNS_A = (
    r'{job="netmon", kind="dns"} |~ " (reply|cached|config) " '
    r"| regexp `\d+ \S+ (?:reply|cached|config) (?P<rname>\S+) is (?P<ip>[0-9a-fA-F.:]+)\s*$`"
    ' | ip != ""'
)
# Same patterns as FLAGS in netmon_report.py, anchored for LogQL
AUTHOR = r"(?i)(.*\\.)?(openmausbot\\.com|mausbot\\.(com|page))|omb-[a-z0-9-]+\\.fly\\.dev|.*milindsoni201\\.workers\\.dev"
TELEMETRY = r"(?i)(.*\\.)?(posthog\\.com|sentry\\.io|statsig(api)?\\.(com|net)|segment\\.io|datadoghq\\.com)"

_ids = iter(range(1, 100))


def target(expr, ref="A", instant=True, legend=None):
    t = {"datasource": DS, "refId": ref, "expr": expr, "queryType": "instant" if instant else "range"}
    if legend:
        t["legendFormat"] = legend
    return t


def stat(title, expr, x, w=6, red=False):
    steps = [{"color": "green", "value": None}] + ([{"color": "red", "value": 1}] if red else [])
    return {
        "id": next(_ids), "type": "stat", "title": title, "datasource": DS,
        "gridPos": {"x": x, "y": 0, "w": w, "h": 4},
        "targets": [target(f"sum(count_over_time({expr} [$__range])) or vector(0)")],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background" if red else "value",
                    "graphMode": "none", "textMode": "value"},
        "fieldConfig": {"defaults": {"unit": "none", "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
    }


def table(title, targets, y, x=0, w=12, h=9, order=(), desc=None):
    order = list(order) + ["Value"]
    panel = {
        "id": next(_ids), "type": "table", "title": title, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": targets,
        "transformations": [
            {"id": "labelsToFields", "options": {"mode": "columns"}},
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True},
                "indexByName": {name: i for i, name in enumerate(order)},
                "renameByName": {"Value": "count"},
            }},
            {"id": "sortBy", "options": {"sort": [{"field": "count", "desc": True}]}},
        ],
        "options": {"showHeader": True, "cellHeight": "sm"},
        "fieldConfig": {"defaults": {}, "overrides": []},
    }
    if desc:
        panel["description"] = desc
    return panel


def topk(by, expr):
    return f"topk(100, sum by ({by}) (count_over_time({expr} [$__range])))"


panels = [
    stat("omb outbound connections", OMB_CONN, 0),
    stat("DNS queries", DNS_Q, 6),
    stat("edge inbound connections", EDGE_CONN, 12),
    stat("Flagged DNS queries (AUTHOR / TELEMETRY)", f'{DNS_Q} | qname =~ "{AUTHOR}|{TELEMETRY}"', 18, red=True),
    table("omb outbound: destination IP / port", [target(topk("dst, dport, proto", OMB_CONN))], 4,
          order=["dst", "dport", "proto"],
          desc="Loopback excluded. Look up the IP in 'DNS answers' (or run netmon_report.py for the joined host view)."),
    table("DNS answers: name → IP", [target(topk("rname, ip", DNS_A))], 4, x=12,
          order=["rname", "ip"], desc="rname is the name in the answer (a CNAME target when there is one)."),
    {
        "id": next(_ids), "type": "timeseries", "title": "omb outbound connections over time", "datasource": DS,
        "gridPos": {"x": 0, "y": 13, "w": 24, "h": 8},
        "targets": [target(f"sum by (dst, dport) (count_over_time({OMB_CONN} [$__auto]))", instant=False,
                           legend="{{dst}}:{{dport}}")],
        "fieldConfig": {"defaults": {"custom": {"drawStyle": "bars", "stacking": {"mode": "normal"}, "fillOpacity": 80},
                                     "unit": "none"}, "overrides": []},
        "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["sum"]},
                    "tooltip": {"mode": "multi"}},
    },
    table("DNS queries", [target(topk("qname, qtype", DNS_Q))], 21, order=["qname", "qtype"]),
    table("Flagged DNS queries", [
        target(f'sum by (flag, qname) (count_over_time({DNS_Q} | qname =~ "{AUTHOR}" | label_format flag="AUTHOR" [$__range]))', "A"),
        target(f'sum by (flag, qname) (count_over_time({DNS_Q} | qname =~ "{TELEMETRY}" | label_format flag="TELEMETRY" [$__range]))', "B"),
    ], 21, x=12, order=["flag", "qname"], desc="Author's own domains and telemetry endpoints (FLAGS in netmon_report.py). Empty is good."),
    table("edge inbound: source IP → local port", [target(topk("src, dport, proto", EDGE_CONN))], 30,
          order=["src", "dport", "proto"],
          desc="On Docker Desktop every connection from the host shows up as the gateway IP."),
    {
        "id": next(_ids), "type": "logs", "title": "Raw events", "datasource": DS,
        "gridPos": {"x": 12, "y": 30, "w": 12, "h": 9},
        "targets": [target('{job="netmon", name=~"$name", kind=~"$kind"}', instant=False)],
        "options": {"showTime": True, "wrapLogMessage": False, "sortOrder": "Descending", "enableLogDetails": True},
    },
]

dashboard = {
    "uid": "netmon",
    "title": "OpenMausBot netmon",
    "description": "Connections and DNS lookups recorded by netmon (conntrack + dnsmasq). See plan.md Phase 1.6.",
    "tags": ["netmon"],
    "editable": False,
    "schemaVersion": 41,
    "time": {"from": "now-24h", "to": "now"},
    "refresh": "1m",
    "timezone": "browser",
    "templating": {"list": [
        {"name": "name", "type": "custom", "query": "omb,edge", "current": {"text": "All", "value": "$__all"},
         "includeAll": True, "allValue": ".+", "multi": False, "label": "netmon"},
        {"name": "kind", "type": "custom", "query": "conn,dns", "current": {"text": "All", "value": "$__all"},
         "includeAll": True, "allValue": ".+", "multi": False, "label": "kind"},
    ]},
    "panels": panels,
}

out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "grafana" / "dashboards" / "netmon.json"
out.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {out} ({len(panels)} panels)")
