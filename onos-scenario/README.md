b# SDN vs Legacy – Triangle Topology Comparison

## Overview

This scenario uses **Mininet + Open vSwitch** to run a controlled side-by-side
comparison between a legacy static network and a software-defined network across
three real metrics:

| Test | What is measured |
|------|-----------------|
| **T1 Bandwidth** | iperf TCP throughput between h1 and h2 |
| **T2 Congestion** | Ping RTT and packet loss while 12 Mbps background traffic runs |
| **T3 Failover** | Recovery time after the primary link is cut |


## Topology

```
           [10 Mbps / 5 ms]
  h1 ── s1 ══════════════════ s2 ── h2     ← primary (bottleneck)
         └──[15M/10ms]─ s3 ─[15M/10ms]──┘  ← alternate
```

| Link | BW | Delay | Used by |
|------|----|-------|---------|
| s1─s2 | 10 Mbps | 5 ms | Primary (legacy default, SDN ICMP) |
| s1─s3─s2 | 15 Mbps | 10 ms + 10 ms | SDN TCP & failover |


## How the Modes Work

### Legacy mode
- All flows are statically installed on the **primary path only** (s1→s2).
- The alternate path through s3 is completely unused.
- Link failure: waits **30 seconds** (simulated STP reconvergence) before flows
  are manually updated to use the alternate path.

### SDN mode (OpenFlow 1.3)
- **ICMP traffic** → primary path (latency policy, 10 ms RTT).
- **TCP traffic** → alternate path (bandwidth policy, 15 Mbps headroom).
- **Fast-failover group** on s1: if primary port goes down, OVS switches to the
  alternate bucket locally — **no controller round-trip required** (~300 ms).


## Expected Results

```
══════════════════════════════════════════════════════════════════════
  METRIC                       LEGACY        SDN        DELTA
  ──────────────────────────────────────────────────────────────────
  Bandwidth (Mbps)               9.4         14.2        +51%
  Latency under load (ms)       80.3         12.1        -85%
  Packet Loss (%)               45.0          0.0       -100%
  Failover time (s)             30.0          0.3        -99%
══════════════════════════════════════════════════════════════════════
```

> Numbers will vary slightly by machine.  The directional advantage is
> consistent: SDN wins on all four metrics in this topology.


## Setup

### 1. Install dependencies

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf
```

Verify OVS is running:
```bash
sudo ovs-vsctl show
```

### 2. Run the automated comparison

```bash
sudo python3 scenario.py
```

This will:
1. Build the triangle topology in Mininet.
2. Run T1/T2/T3 in legacy mode.
3. Reinstall flows in SDN mode.
4. Run T1/T2/T3 again.
5. Print a side-by-side summary table.

Total runtime ≈ 4–5 minutes (dominated by the 30-second STP simulation).

### 3. Drop into the CLI for manual exploration

```bash
sudo python3 scenario.py --cli
```

Useful CLI commands once inside:
```
mininet> pingall                        # basic reachability
mininet> h1 ping -c5 h2                 # ICMP latency
mininet> h2 iperf -s -D && h1 iperf -c h2 -t8   # bandwidth
mininet> link s1 s2 down               # simulate failure
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1  # inspect flow table
mininet> sh ovs-ofctl -O OpenFlow13 dump-groups s1 # inspect group table
```


## Using ONOS as the Controller

If you have ONOS running (default port 8181 / 6653):

```bash
# 1. Start Mininet connected to ONOS
ONOS_IP=127.0.0.1 bash onos_intents.sh mininet

# 2. In another terminal, push SDN intents
ONOS_IP=127.0.0.1 bash onos_intents.sh setup

# 3. Check intent state
ONOS_IP=127.0.0.1 bash onos_intents.sh check

# 4. Remove intents when done
ONOS_IP=127.0.0.1 bash onos_intents.sh teardown
```

ONOS apps required: `openflow`, `fwd`  
Activate via UI → Apps, or:
```bash
curl -u onos:rocks -X POST http://127.0.0.1:8181/onos/v1/applications/org.onosproject.openflow/active
curl -u onos:rocks -X POST http://127.0.0.1:8181/onos/v1/applications/org.onosproject.fwd/active
```


## Inspecting Flow Tables Manually

After running `setup_legacy()` or `setup_sdn()` from the CLI:

```bash
# Show all flows on s1
sudo ovs-ofctl -O OpenFlow13 dump-flows s1

# Show group tables (fast-failover — SDN only)
sudo ovs-ofctl -O OpenFlow13 dump-groups s1

# Watch live packet/byte counters update
watch -n1 'sudo ovs-ofctl -O OpenFlow13 dump-flows s1'
```


## Key Concepts Illustrated

| Concept | Where it appears |
|---------|-----------------|
| **Traffic classification** | ICMP vs TCP sent to different paths |
| **Path selection** | Alternate path used when more BW needed |
| **Fast-failover groups** | OFv1.3 Group Table type=ff on s1 |
| **STP reconvergence delay** | 30-second gap in legacy failover test |
| **QoS / policy routing** | Priority-based flow rules on s1 |
| **Centralized control** | One place (controller) governs entire fabric |
