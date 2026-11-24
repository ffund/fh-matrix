#!/bin/bash
# fh-tap.sh [TAP_MODE=dpdk|bridge]   Startup service for the tap node. Bridges its two fronthaul ports (10.10.1.11 towards the DU,
# 10.10.1.12 towards the RU host) so that every eCPRI frame passes through this node, where fh-tapcap.sh
# records it. The DU and the RU host then run with no capture of their own.
set -u
for kv in "$@"; do export "$kv"; done
RUN=/opt/fh/run; sudo mkdir -p $RUN /dev/shm/cap; sudo chmod a+rwx $RUN /dev/shm/cap
exec > >(tee -a $RUN/fh-tap.log) 2>&1
echo "=== fh-tap $(date -Is) kernel $(uname -r)"
[ -f /opt/fh/scripts/smi_rate.py ] && sudo python3 /opt/fh/scripts/smi_rate.py
find_if(){ ip -o -4 addr show | awk -v ip="$1" '{split($4,a,"/"); if (a[1]==ip) print $2}' | head -1; }
for i in $(seq 1 ${WAIT_TRIES:-60}); do A=$(find_if 10.10.1.11); B=$(find_if 10.10.1.12); [ -n "$A" ] && [ -n "$B" ] && break; sleep 2; done
# On a rerun the ports have lost their addresses (bridged or handed to DPDK): use the names saved the first time.
if [ -z "${A:-}" ] || [ -z "${B:-}" ]; then
  [ -f $RUN/tap_du_if ] && [ -f $RUN/tap_ru_if ] && { A=$(cat $RUN/tap_du_if); B=$(cat $RUN/tap_ru_if); }
  systemctl stop fh-tap-dpdk 2>/dev/null
  for I in ${A:-} ${B:-}; do p=$(cat $RUN/pci_$I 2>/dev/null); [ -n "$p" ] && dpdk-devbind.py -b i40e $p 2>/dev/null; done; sleep 3
fi
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
# The tap's clock follows the DU's grandmaster when the profile gives it a port on the PTP link (10.10.2.3), so
# capture timestamps line up with the DU's and the RU's clocks.
PTPIF=$(find_if 10.10.2.3)
if [ -n "$PTPIF" ]; then
  D=/opt/fh/twonode; sudo cp /local/repository/etc/fh/ptp-slave-udp.cfg $D/
  echo "$D/*.cfg r," | sudo tee /etc/apparmor.d/local/usr.sbin.ptp4l >/dev/null; sudo apparmor_parser -r /etc/apparmor.d/usr.sbin.ptp4l 2>/dev/null
  grep -q '^  /run/ptp4l rw,' /etc/apparmor.d/usr.sbin.phc2sys || sudo sed -i '$ s|^}|  /run/ptp4l rw,\n}|' /etc/apparmor.d/usr.sbin.phc2sys
  sudo apparmor_parser -r /etc/apparmor.d/usr.sbin.phc2sys 2>/dev/null
  sudo systemctl disable --now chrony >/dev/null 2>&1; sudo timedatectl set-ntp false 2>/dev/null
  sudo systemctl stop fh-ptp4l fh-phc2sys 2>/dev/null
  sudo systemd-run --unit=fh-ptp4l --collect -q bash -c "ptp4l -i $PTPIF -f $D/ptp-slave-udp.cfg -m -H > $D/ptp4l.log 2>&1"
  sleep 30; sudo systemd-run --unit=fh-phc2sys --collect -q bash -c "phc2sys -s $PTPIF -w -m -r -r -n 24 > $D/phc2sys.log 2>&1"
  sleep 10; echo "PTP slave on $PTPIF: $(tail -1 $D/ptp4l.log | cut -c1-90)"
fi
# Default: hand the two ports to DPDK (fh-tap-dpdk.sh). TAP_MODE=bridge keeps the kernel bridge.
if [ "${TAP_MODE:-dpdk}" = dpdk ]; then
  sudo /local/repository/bin/fh-tap-dpdk.sh start
  echo "=== fh-tap done (DPDK). Capture with: sudo /local/repository/bin/fh-tapcap-dpdk.sh <name> [seconds]"
else
  echo "=== fh-tap done (kernel bridge $A <-> $B). Capture with: sudo /local/repository/bin/fh-tapcap.sh <name> [seconds]"
fi
