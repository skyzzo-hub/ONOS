#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  ONOS Intent Configuration for SDN vs Legacy Comparison
#  Use this script INSTEAD of the manual ovs-ofctl flow rules
#  when ONOS is running as the remote controller.
# ─────────────────────────────────────────────────────────────────────────────
#
#  Prerequisites
#  ─────────────
#  1. ONOS running (local or remote).  Set ONOS_IP below.
#  2. Mininet started WITHOUT a controller, then reconnect:
#       sudo python3 scenario.py --no-autoflow
#  3. ONOS applications activated:
#       openflow  (handles OF switch connections)
#       fwd       (default forwarding – we will override with intents)
#
#  Topology Device IDs (ONOS assigns these via DPID)
#    s1 → of:0000000000000001
#    s2 → of:0000000000000002
#    s3 → of:0000000000000003
#
#  Usage
#  ─────
#    bash onos_intents.sh setup     # install all intents
#    bash onos_intents.sh teardown  # remove all intents
#    bash onos_intents.sh check     # show intent state
# ─────────────────────────────────────────────────────────────────────────────

ONOS_IP="${ONOS_IP:-127.0.0.1}"
ONOS_PORT="${ONOS_PORT:-8181}"
BASE="http://${ONOS_IP}:${ONOS_PORT}/onos/v1"
AUTH="-u onos:rocks"
CT='-H "Content-Type: application/json"'

# Colour helpers
RED='\033[91m'; GRN='\033[92m'; YEL='\033[93m'; RST='\033[0m'

# ── Activate required applications ───────────────────────────────────────────
activate_apps() {
    echo -e "${YEL}Activating ONOS applications …${RST}"
    for app in org.onosproject.openflow org.onosproject.fwd; do
        curl -sSf $AUTH -X POST "${BASE}/applications/${app}/active" > /dev/null
        echo "  ✓ ${app}"
    done
}

# ── Intent helpers ────────────────────────────────────────────────────────────
post_intent() {
    local KEY="$1"
    local PAYLOAD="$2"
    curl -sSf $AUTH \
         -H "Content-Type: application/json" \
         -X POST "${BASE}/intents" \
         -d "${PAYLOAD}" | python3 -m json.tool 2>/dev/null || true
    echo "  → Intent ${KEY} submitted"
}

# ── Setup ─────────────────────────────────────────────────────────────────────
setup() {
    echo -e "${GRN}─── Installing ONOS Intents ───${RST}"
    activate_apps

    # ── Intent 1: h1→h2 ICMP via PRIMARY path (low latency) ─────────────────
    #   Explicit route constraint: s1 → s2  (no s3 hop)
    post_intent "icmp-fwd" '{
      "type": "PointToPointIntent",
      "appId": "org.onosproject.cli",
      "key": "icmp-h1-h2-primary",
      "priority": 300,
      "ingressPoint": { "device": "of:0000000000000001", "port": "1" },
      "egressPoint":  { "device": "of:0000000000000002", "port": "1" },
      "selector": {
        "criteria": [
          { "type": "ETH_TYPE",  "ethType": "0x0800" },
          { "type": "IP_PROTO",  "protocol": 1 }
        ]
      },
      "treatment": { "instructions": [] },
      "constraints": [
        {
          "type": "ExplicitPathConstraint",
          "links": [
            {
              "src": { "device": "of:0000000000000001", "port": "2" },
              "dst": { "device": "of:0000000000000002", "port": "2" }
            }
          ]
        }
      ]
    }'

    # ── Intent 2: h1→h2 TCP via ALTERNATE path (more bandwidth) ─────────────
    #   Explicit route: s1 → s3 → s2
    post_intent "tcp-fwd" '{
      "type": "PointToPointIntent",
      "appId": "org.onosproject.cli",
      "key": "tcp-h1-h2-alternate",
      "priority": 250,
      "ingressPoint": { "device": "of:0000000000000001", "port": "1" },
      "egressPoint":  { "device": "of:0000000000000002", "port": "1" },
      "selector": {
        "criteria": [
          { "type": "ETH_TYPE",  "ethType": "0x0800" },
          { "type": "IP_PROTO",  "protocol": 6 }
        ]
      },
      "treatment": { "instructions": [] },
      "constraints": [
        {
          "type": "ExplicitPathConstraint",
          "links": [
            {
              "src": { "device": "of:0000000000000001", "port": "3" },
              "dst": { "device": "of:0000000000000003", "port": "1" }
            },
            {
              "src": { "device": "of:0000000000000003", "port": "2" },
              "dst": { "device": "of:0000000000000002", "port": "3" }
            }
          ]
        }
      ]
    }'

    # ── Intent 3: Bandwidth-constrained intent for background traffic ─────────
    #   BandwidthConstraint ensures the alternate path can carry the load
    post_intent "bw-constrained" '{
      "type": "PointToPointIntent",
      "appId": "org.onosproject.cli",
      "key": "bw-h1-h2-alternate",
      "priority": 200,
      "ingressPoint": { "device": "of:0000000000000001", "port": "1" },
      "egressPoint":  { "device": "of:0000000000000002", "port": "1" },
      "selector": { "criteria": [] },
      "treatment": { "instructions": [] },
      "constraints": [
        { "type": "BandwidthConstraint", "bandwidth": 12000000 },
        { "type": "LatencyConstraint",   "latencyMillis": 50 }
      ]
    }'

    echo -e "\n${GRN}✓ Intents submitted. Check state with: bash onos_intents.sh check${RST}"
}

# ── Teardown ──────────────────────────────────────────────────────────────────
teardown() {
    echo -e "${YEL}─── Removing ONOS Intents ───${RST}"
    for key in icmp-h1-h2-primary tcp-h1-h2-alternate bw-h1-h2-alternate; do
        curl -sSf $AUTH -X DELETE "${BASE}/intents/org.onosproject.cli/${key}" || true
        echo "  ✗ Removed ${key}"
    done
}

# ── Check ─────────────────────────────────────────────────────────────────────
check() {
    echo -e "${YEL}─── Current Intent State ───${RST}"
    curl -sSf $AUTH "${BASE}/intents" | python3 -c "
import json,sys
data = json.load(sys.stdin)
intents = data.get('intents', [])
print(f'  Total intents: {len(intents)}')
for i in intents:
    state = i.get('state','?')
    key   = i.get('key','?')
    prio  = i.get('priority','?')
    color = '\033[92m' if state == 'INSTALLED' else '\033[91m'
    print(f'  {color}{state:<15}\033[0m  key={key}  priority={prio}')
" 2>/dev/null || curl -sSf $AUTH "${BASE}/intents"
}

# ── Mininet launch helper ─────────────────────────────────────────────────────
launch_mininet() {
    echo -e "${YEL}Launching Mininet connected to ONOS at ${ONOS_IP}:6653 …${RST}"
    sudo python3 - <<PYEOF
from mininet.net  import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.topo import Topo
from mininet.cli  import CLI
import sys; sys.path.insert(0, '.')
from scenario import TriangleTopo

topo = TriangleTopo()
net  = Mininet(
    topo       = topo,
    controller = lambda name: RemoteController(name, ip='${ONOS_IP}', port=6653),
    link       = TCLink,
    autoSetMacs= False
)
net.start()
net.get('h1').cmd('arp -s 10.0.0.2 00:00:00:00:00:02')
net.get('h2').cmd('arp -s 10.0.0.1 00:00:00:00:00:01')
print("Mininet ready. ONOS should see 3 switches now.")
CLI(net)
net.stop()
PYEOF
}

# ── Entry point ───────────────────────────────────────────────────────────────
case "${1:-}" in
    setup)    setup    ;;
    teardown) teardown ;;
    check)    check    ;;
    mininet)  launch_mininet ;;
    *)
        echo "Usage: $0 {setup|teardown|check|mininet}"
        echo ""
        echo "  setup    – post all path intents to ONOS"
        echo "  teardown – remove all intents"
        echo "  check    – show intent install state"
        echo "  mininet  – launch Mininet connected to ONOS"
        echo ""
        echo "  Environment variables:"
        echo "    ONOS_IP=${ONOS_IP}   ONOS_PORT=${ONOS_PORT}"
        ;;
esac
