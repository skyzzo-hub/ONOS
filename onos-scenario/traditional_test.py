from mininet.net import Mininet
from mininet.node import Node
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel
import time
import re


class LinuxRouter(Node):
    def config(self, **params):
        super().config(**params)
        self.cmd('sysctl -w net.ipv4.ip_forward=1')

    def terminate(self):
        self.cmd('sysctl -w net.ipv4.ip_forward=0')
        super().terminate()


def run_tests(net, h1, h2):
    print("\n===== WARMUP =====")
    h1.cmd(f'ping -c 3 {h2.IP()}')

    # ── Congest the network then measure impact ────────────────────────────────
    print("\n===== CONGESTION TEST =====")
    print("Starting iperf flood in background (saturating path A)...")
    h2.cmd('iperf -s -u -D')
    time.sleep(1)
    h1.cmd(f'iperf -c {h2.IP()} -u -b 100M -t 30 &')
    time.sleep(3)

    print("Measuring latency under congestion (static routes cannot reroute)...")
    congested_out = h1.cmd(f'ping -c 10 {h2.IP()}')
    print(congested_out)
    rtt_match2 = re.search(r'rtt .* = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', congested_out)
    loss_match  = re.search(r'(\d+)% packet loss', congested_out)
    if rtt_match2:
        print(f'Congested avg RTT: {rtt_match2.group(2)} ms')
    if loss_match:
        print(f'Packet loss under congestion: {loss_match.group(1)}%')

    # Stop flood and measure recovery
    h1.cmd('pkill iperf 2>/dev/null')
    h2.cmd('pkill iperf 2>/dev/null')
    time.sleep(2)

    print("\n===== POST-CONGESTION LATENCY =====")
    recovery_out = h1.cmd(f'ping -c 10 {h2.IP()}')
    print(recovery_out)
    rtt_match3 = re.search(r'rtt .* = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', recovery_out)
    if rtt_match3:
        print(f'Post-congestion avg RTT: {rtt_match3.group(2)} ms') 

    # ── Link failure ───────────────────────────────────────────────────────────
    print("\n===== LINK FAILURE TEST =====")
    print("Pinging h2 continuously, then bringing Path A (r1-r2) down...")
    print("No dynamic routing configured — traffic will drop until link recovers.\n")
 
    h1.cmd(f'ping -i 0.5 {h2.IP()} > /tmp/ping_failure.log 2>&1 &')
    time.sleep(3)
 
    print(">> Bringing r1-r2 link DOWN...")
    net.configLinkStatus('r1', 'r2', 'down')
    time.sleep(8)
 
    print(">> Bringing r1-r2 link back UP...")
    net.configLinkStatus('r1', 'r2', 'up')
    time.sleep(3)
 
    h1.cmd('pkill ping 2>/dev/null')
    time.sleep(0.5)
 
    ping_log = h1.cmd('cat /tmp/ping_failure.log')
    print(ping_log)
 
    lost   = ping_log.count('no answer yet') + ping_log.count('Request timeout')
    seqs   = re.findall(r'icmp_seq=(\d+)', ping_log)
    print(f'Packets sent    : {len(seqs)}')
    print(f'Packets lost    : {lost}  (Path B unused — all loss is unrecovered)')    


def build_network():
    net = Mininet(link=TCLink)

    print("Creating hosts...")
    h1 = net.addHost('h1', ip='10.0.1.2/24', defaultRoute='via 10.0.1.1')
    h2 = net.addHost('h2', ip='10.0.6.2/24', defaultRoute='via 10.0.6.1')

    print("Creating routers...")
    r1 = net.addHost('r1', cls=LinuxRouter)
    r2 = net.addHost('r2', cls=LinuxRouter)
    r3 = net.addHost('r3', cls=LinuxRouter)
    r4 = net.addHost('r4', cls=LinuxRouter)

    # Topology:
    #
    #   h1 -- r1 -- r2 -- r3 -- h2
    #          \              /
    #           ---- r4 -----
    #
    # Two paths from h1 to h2:
    #   Path A (primary):  h1 - r1 - r2 - r3 - h2   (low latency, 2ms/hop)
    #   Path B (unused):   h1 - r1 - r4 - r3 - h2   (higher latency, 5ms/hop)
    #
    # Static routes are configured for Path A only.
    # Path B links exist but are unreachable — no static route points to them.
    # Under congestion, traffic stays on Path A with no automatic rerouting.

    print("Creating links...")
    # Host links
    net.addLink(h1, r1, intfName1='r1-eth0',  bw=10, delay='2ms')
    net.addLink(r3, h2, intfName1='r3-eth0',  bw=10, delay='2ms')

    # Path A
    net.addLink(r1, r2, intfName1='r1-r2', intfName2='r2-r1', bw=10, delay='2ms')
    net.addLink(r2, r3, intfName1='r2-r3', intfName2='r3-r2', bw=10, delay='2ms')

    # Path B (physically present, no routes configured)
    net.addLink(r1, r4, intfName1='r1-r4', intfName2='r4-r1', bw=10, delay='5ms')
    net.addLink(r4, r3, intfName1='r4-r3', intfName2='r3-r4', bw=10, delay='5ms')

    net.start()

    print("Configuring router interfaces...")
    # Path A interfaces
    r1.cmd('ifconfig r1-eth0 10.0.1.1/24')
    r1.cmd('ifconfig r1-r2 10.0.12.1/24')
    r2.cmd('ifconfig r2-r1 10.0.12.2/24')
    r2.cmd('ifconfig r2-r3 10.0.23.1/24')
    r3.cmd('ifconfig r3-r2 10.0.23.2/24')
    r3.cmd('ifconfig r3-eth0 10.0.6.1/24')


    # Path B interfaces (addressed but no routes use them)
    r1.cmd('ifconfig r1-r4 10.0.14.1/24')
    r4.cmd('ifconfig r4-r1 10.0.14.2/24')
    r4.cmd('ifconfig r4-r3 10.0.43.1/24')
    r3.cmd('ifconfig r3-r4 10.0.43.2/24')

    print("Configuring static routes (Path A only)...")
    # Forward: h1 -> h2
    r1.cmd('ip route add 10.0.6.0/24 via 10.0.12.2')
    r2.cmd('ip route add 10.0.6.0/24 via 10.0.23.2')

    # Reverse: h2 -> h1
    r3.cmd('ip route add 10.0.1.0/24 via 10.0.23.1')
    r2.cmd('ip route add 10.0.1.0/24 via 10.0.12.1')

    time.sleep(2)
    run_tests(net, h1, h2)
    CLI(net)
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    build_network()
