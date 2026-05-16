from functools import partial
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
import time
import re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ─── Configuration ────────────────────────────────────────────────────────────
TOTAL_RUNS       = 13        # 12 batches: 5, 10, 15 … 60 hosts
BATCH_SIZE       = 5
SWITCHES_NUMBER  = 20
HOSTS_PER_SWITCH = 3

# Kept deliberately short — we want reactive-mode latency, not cached-flow latency.
# A warm, fully-converged ping series defeats the purpose of the test.
PING_COUNT       = 5

IPERF_DURATION   = 8

CONTROLLER_IP    = '172.17.0.2'
CONTROLLER_PORT  = 6653

RESULTS_FILE     = 'sdn_results.txt'
GRAPH_FILE       = 'sdn_scalability.png'
# ──────────────────────────────────────────────────────────────────────────────


class CustomTopo(Topo):
    def build(self, num_hosts=0):
        switches = []
        for i in range(SWITCHES_NUMBER):
            sw = self.addSwitch(f's{i}', cls=OVSSwitch, protocols='OpenFlow14')
            switches.append(sw)
            if i > 0:
                self.addLink(switches[i - 1], switches[i])
        # No closing link — linear chain, not a ring.
        # A ring lets ONOS pick the shorter direction once the destination
        # crosses the midpoint (~s10): at 35 hosts the last host lands on s11,
        # ONOS routes back via s0→s19→...→s11 (9 hops instead of 11) and
        # latency drops. A chain has exactly one path so hop-count — and
        # therefore latency — grows monotonically with the destination switch.

        for h in range(num_hosts):
            host   = self.addHost(f'h{h:02d}')
            sw_idx = (h // HOSTS_PER_SWITCH) % SWITCHES_NUMBER
            self.addLink(switches[sw_idx], host)


def flush_flows(net):
    for sw in net.switches:
        sw.cmd(f'ovs-ofctl del-flows {sw.name}')
    time.sleep(0.3)   


def _parse_bw(line):
    """Normalise K / M / G bits/sec → Mbps."""
    m = re.search(r'([\d.]+)\s+(K|M|G)bits/sec', line)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2)
    return v / 1_000 if u == 'K' else v * 1_000 if u == 'G' else v


def measure(net, num_hosts):



    print('  [*] One-time flow flush before first measurement ...')
    flush_flows(net) 

    src    = net.get('h00')
    dst    = net.get(f'h{num_hosts - 1:02d}')
    dst_ip = dst.IP()

    # ── Ping ──────────────────────────────────────────────────────────────────
    print(f'  [*] Ping  h00 → h{num_hosts-1:02d}  ({PING_COUNT} pkts) ...')
    out = src.cmd(f'ping -c {PING_COUNT} -W 2 {dst_ip}')
    rtt = None
    for line in out.split('\n'):
        if 'rtt min/avg/max' in line:
            rtt = float(line.split('/')[4])   # avg field
            break
    if rtt is None:
        print('  [!] Ping failed — 100 % loss')

    # ── iPerf — flows are now installed so bandwidth is meaningful ────────────
    print(f'  [*] iPerf h00 → h{num_hosts-1:02d}  ({IPERF_DURATION}s TCP) ...')
    dst.cmd('killall -q iperf')
    dst.cmd(f'iperf -s -B {dst_ip} -D > /tmp/iperf_srv.log 2>&1')
    time.sleep(1.0)   # give the daemon a moment to bind

    out = src.cmd(f'iperf -c {dst_ip} -t {IPERF_DURATION} 2>&1')
    dst.cmd('killall -q iperf')

    bw = None
    for line in reversed(out.split('\n')):
        bw = _parse_bw(line)
        if bw is not None:
            break
    if bw is None:
        print(f'  [!] iPerf parse failed. Raw:\n{out.strip()}')

    return rtt, bw


def save_results(results):
    with open(RESULTS_FILE, 'w') as f:
        f.write(f'SDN Scalability — {time.strftime("%Y-%m-%d %H:%M:%S")}\n')
        f.write(f'Ping: {PING_COUNT} pkts (reactive mode)   iPerf: {IPERF_DURATION}s TCP\n')
        f.write(f'Path: h00 → h{{last}}  (flow tables flushed before each run)\n')
        f.write('=' * 44 + '\n')
        f.write(f'  {"Hosts":>6}  {"RTT (ms)":>10}  {"BW (Mbps)":>10}\n')
        f.write('  ' + '-' * 32 + '\n')
        for hosts, rtt, bw in results:
            rtt_s = f'{rtt:.3f}' if rtt is not None else 'N/A'
            bw_s  = f'{bw:.3f}'  if bw  is not None else 'N/A'
            f.write(f'  {hosts:>6}  {rtt_s:>10}  {bw_s:>10}\n')
        f.write('=' * 44 + '\n')
    print(f'[*] Results saved → {RESULTS_FILE}')


def plot(results):
    x    = [r[0] for r in results]
    rtts = [r[1] for r in results]
    bws  = [r[2] for r in results]

    fig, ax1 = plt.subplots(figsize=(10, 5))

    ax1.set_xlabel('Number of Hosts', fontsize=12)
    ax1.set_ylabel('Avg RTT (ms)', color='royalblue', fontsize=12)
    ax1.plot(x, rtts, marker='o', color='royalblue',
             linewidth=2, markersize=7, label='Latency (ms)')
    ax1.tick_params(axis='y', labelcolor='royalblue')
    ax1.grid(True, alpha=0.3)

    ax2 = ax1.twinx()
    ax2.set_ylabel('Bandwidth (Mbps)', color='seagreen', fontsize=12)
    bw_pts = [(xi, bi) for xi, bi in zip(x, bws) if bi is not None]
    if bw_pts:
        bx, by = zip(*bw_pts)
        ax2.plot(bx, by, marker='s', color='seagreen',
                 linewidth=2, markersize=7, label='Bandwidth (Mbps)')
    ax2.tick_params(axis='y', labelcolor='seagreen')

    l1, lb1 = ax1.get_legend_handles_labels()
    l2, lb2 = ax2.get_legend_handles_labels()
    ax1.legend(l1 + l2, lb1 + lb2, loc='center left', fontsize=10)

    plt.title('SDN Scalability — Latency & Bandwidth vs Number of Hosts\n'
              '(reactive mode: flow tables flushed before each batch)', fontsize=11)
    plt.tight_layout()
    plt.savefig(GRAPH_FILE, dpi=150)
    print(f'[*] Graph saved → {GRAPH_FILE}')
    plt.close(fig)


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    results = []
    _flushed = False   # guard — flush happens inside the first net.start()

    for batch in range(1, TOTAL_RUNS):
        num_hosts = batch * BATCH_SIZE
        print(f'\n{"="*50}')
        print(f'  Batch {batch}/{TOTAL_RUNS-1} — {num_hosts} hosts')
        print(f'{"="*50}')

        topo = CustomTopo(num_hosts=num_hosts)
        net  = Mininet(
            topo=topo,
            controller=lambda name: RemoteController(
                name, ip=CONTROLLER_IP, port=CONTROLLER_PORT),
            switch=partial(OVSSwitch, protocols='OpenFlow14'),
            autoSetMacs=True
        )
        net.start()

        print(f'  [*] Waiting 10s for ONOS to discover topology ...')
        time.sleep(10)

        # Flush once — at the very start of batch 1 only.
        # This clears any stale flows left by a previous run so the first
        # packet-in events are genuine. From batch 2 onward, flows accumulate
        # naturally as ONOS installs them, which is exactly the real-world
        # behaviour we want to measure.
        if not _flushed:
            print('  [*] One-time flow flush before first measurement ...')
            flush_flows(net)
            _flushed = True

        rtt, bw = measure(net, num_hosts)

        rtt_s = f'{rtt:.3f}ms' if rtt is not None else 'N/A'
        bw_s  = f'{bw:.3f}Mbps' if bw is not None else 'N/A'
        print(f'  [=] RTT={rtt_s}  BW={bw_s}')

        results.append((num_hosts, rtt, bw))

        net.stop()
        time.sleep(3)

    save_results(results)
    plot(results)
