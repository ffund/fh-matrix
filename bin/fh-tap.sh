#!/bin/bash
# fh-tap.sh   Startup service for the tap node. Bridges its two fronthaul ports (10.10.1.11 towards the DU,
# 10.10.1.12 towards the RU host) so that every eCPRI frame passes through this node, where fh-tapcap.sh
# records it. The DU and the RU host then run with no capture of their own.
set -u
RUN=/opt/fh/run; sudo mkdir -p $RUN /dev/shm/cap; sudo chmod a+rwx $RUN /dev/shm/cap
exec > >(tee -a $RUN/fh-tap.log) 2>&1
echo "=== fh-tap $(date -Is) kernel $(uname -r)"
[ -f /opt/fh/scripts/smi_rate.py ] && sudo python3 /opt/fh/scripts/smi_rate.py
find_if(){ ip -o -4 addr show | awk -v ip="$1" '{split($4,a,"/"); if (a[1]==ip) print $2}' | head -1; }
for i in $(seq 1 60); do A=$(find_if 10.10.1.11); B=$(find_if 10.10.1.12); [ -n "$A" ] && [ -n "$B" ] && break; sleep 2; done
[ -n "${A:-}" ] && [ -n "${B:-}" ] || { echo "!! tap ports not found (need 10.10.1.11 and 10.10.1.12)"; exit 1; }
echo "$A" | sudo tee $RUN/tap_du_if >/dev/null; echo "$B" | sudo tee $RUN/tap_ru_if >/dev/null
sudo systemctl stop irqbalance 2>/dev/null
for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance | sudo tee $g >/dev/null 2>&1; done
for s in /sys/devices/system/cpu/cpu*/cpuidle/state[1-9]/disable; do echo 1 | sudo tee $s >/dev/null 2>&1; done
sudo ip link add name br-fh type bridge 2>/dev/null
sudo ip link set br-fh type bridge stp_state 0 forward_delay 0 ageing_time 30000
for I in $A $B; do
  sudo ip addr flush dev $I
  sudo ip link set $I mtu 9000 promisc on up
  # Offloads that merge or split frames would change what the capture sees and add delay.
  sudo ethtool -K $I gro off gso off tso off lro off rx-vlan-offload off tx-vlan-offload off 2>/dev/null
  sudo ethtool -C $I adaptive-rx off adaptive-tx off rx-usecs 0 tx-usecs 0 2>/dev/null
  sudo ethtool -G $I rx 4096 tx 4096 2>/dev/null
  sudo ip link set $I master br-fh
  echo "$I: numa $(cat /sys/class/net/$I/device/numa_node 2>/dev/null) driver $(ethtool -i $I 2>/dev/null | awk '/^driver/{print $2}')"
done
sudo ip link set br-fh mtu 9000 up
# Do not let bridged frames go through iptables.
sudo sysctl -qw net.bridge.bridge-nf-call-iptables=0 net.bridge.bridge-nf-call-ip6tables=0 net.bridge.bridge-nf-call-arptables=0 2>/dev/null
bridge link show | cut -c1-100
echo "=== fh-tap done: bridging $A (DU side) <-> $B (RU side). Capture with: sudo /local/repository/bin/fh-tapcap.sh <name> [seconds]"
