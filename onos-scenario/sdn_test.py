from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel
import time
import re

CONTROLLER_IP   = '172.17.0.2'
CONTROLLER_PORT = 6653


def run_tests(net, h1, h2):
    print("\n===== WARMUP =====")
    h1.cmd(f'ping -c 3 {h2.IP()}')

    # ── Congest the network then measure impact ────────────────────────────────
    print("\n===== CONGESTION TEST =====")
    print("Starting iperf flood in background (saturating path)...")
    h2.cmd('iperf -s -u -D')
    time.sleep(1)
    h1.cmd(f'iperf -c {h2.IP()} -u -b 100M -t 30 &')
    time.sleep(3)

    print("Measuring latency under congestion (SDN controller may reroute)...")
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
    print("Pinging h2 continuously, then bringing Path A (s1-s2) down...")
    print("ONOS should detect the failure and reroute via Path B (s1-s4-s3).\n")
 
    h1.cmd(f'ping -i 0.5 -c 50 {h2.IP()} > /tmp/ping_failure.log 2>&1 &')
    time.sleep(3)
 
    print(">> Bringing s1-s2 link DOWN...")
    net.configLinkStatus('s1', 's2', 'down')
    time.sleep(8)
 
    print(">> Bringing s1-s2 link back UP...")
    net.configLinkStatus('s1', 's2', 'up')
    time.sleep(3)
 
    h1.cmd('pkill ping 2>/dev/null')
    time.sleep(0.5)
 
    ping_log = h1.cmd('cat /tmp/ping_failure.log')
    print(ping_log)
 
    lost   = ping_log.count('no answer yet') + ping_log.count('Request timeout')
    seqs   = re.findall(r'icmp_seq=(\d+)', ping_log)
    print(f'Packets sent    : {len(seqs)}')
    print(f'Packets lost    : {lost}  (lower = faster SDN reroute)')    


def build_network():
    net = Mininet(controller=None, link=TCLink)

    print("Creating ONOS controller...")
    net.addController(
        'c0',
        controller=RemoteController,
        ip=CONTROLLER_IP,
        port=CONTROLLER_PORT
    )

    print("Creating hosts...")
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')

    print("Creating switches...")
    s1 = net.addSwitch('s1', protocols='OpenFlow14')
    s2 = net.addSwitch('s2', protocols='OpenFlow14')
    s3 = net.addSwitch('s3', protocols='OpenFlow14')
    s4 = net.addSwitch('s4', protocols='OpenFlow14')

    # Topology:
    #
    #   h1 -- s1 -- s2 -- s3 -- h2
    #          \              /
    #           ---- s4 -----
    #
    # Two paths from h1 to h2:
    #   Path A (primary):  h1 - s1 - s2 - s3 - h2   (low latency, 2ms/hop)
    #   Path B (backup):   h1 - s1 - s4 - s3 - h2   (higher latency, 5ms/hop)

    print("Creating links...")
    net.addLink(h1, s1, bw=10, delay='2ms')

    net.addLink(s1, s2, bw=10, delay='2ms')   # path A
    net.addLink(s2, s3, bw=10, delay='2ms')   # path A

    net.addLink(s1, s4, bw=10, delay='5ms')   # path B
    net.addLink(s4, s3, bw=10, delay='5ms')   # path B

    net.addLink(s3, h2, bw=10, delay='2ms')

    net.start()

    print("Waiting for ONOS topology discovery...")
    time.sleep(20)

    run_tests(net, h1, h2)
    CLI(net)
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    build_network()
