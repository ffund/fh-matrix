#!/bin/bash
# fh-tap-dpdk.sh [start|stop|status]   Run as root on the tap node, after fh-tap.sh has found the two ports.
# Replaces the kernel bridge with DPDK: dpdk-testpmd forwards frames between the DU-side and the RU-side port
# ("io" mode, no header changes), polling each port on its own core. dpdk-dumpcap (fh-tapcap-dpdk.sh) attaches
# as a secondary process; the forwarder only enqueues a reference to each frame to it, so the copy and the file
# writing happen outside the forwarding path. Needs: packages dpdk dpdk-dev, an active IOMMU, hugepages.
set -u
RUN=/opt/fh/run; A=$(cat $RUN/tap_du_if); B=$(cat $RUN/tap_ru_if)
pci(){ cat $RUN/pci_$1 2>/dev/null || basename "$(readlink /sys/class/net/$1/device)"; }
PA=$(pci $A); PB=$(pci $B); echo $PA > $RUN/pci_$A; echo $PB > $RUN/pci_$B
NODE=$(cat /sys/bus/pci/devices/$PA/numa_node); [ "$NODE" -ge 0 ] 2>/dev/null || NODE=0
# Three cores on the NIC's NUMA node, skipping CPU 0: main lcore + one forwarding core per direction.
CORES=$(awk -F, '{print $2","$3","$4}' <(cat /sys/devices/system/node/node$NODE/cpulist | python3 -c "
import sys
s=sys.stdin.read().strip(); o=[]
for p in s.split(','):
    a,_,b=p.partition('-'); o+=range(int(a),int(b or a)+1)
print(','.join(map(str,o)))"))
case "${1:-start}" in
start)
  ip link del br-fh 2>/dev/null
  for I in $A $B; do ip link set $I down 2>/dev/null; done
  command -v dpdk-testpmd >/dev/null || { export DEBIAN_FRONTEND=noninteractive; apt-get -qq update; apt-get -y -qq install dpdk dpdk-dev >/dev/null; }
  modprobe vfio-pci
  [ "$(cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages)" -ge 1024 ] || echo 1024 > /sys/devices/system/node/node$NODE/hugepages/hugepages-2048kB/nr_hugepages
  dpdk-devbind.py -b vfio-pci $PA $PB
  dpdk-devbind.py -s | grep -E "$PA|$PB"
  systemctl stop fh-tap-dpdk 2>/dev/null
  # stdin from "sleep infinity" keeps testpmd running non-interactively; --stats-period prints port counters.
  systemd-run --unit=fh-tap-dpdk --collect -q bash -c "sleep infinity | dpdk-testpmd -l $CORES -n 4 -a $PA -a $PB --file-prefix fhtap -- \
     --forward-mode=io --port-topology=paired --nb-cores=2 --rxq=1 --txq=1 --rxd=4096 --txd=4096 --burst=32 \
     --max-pkt-len=9600 --mbuf-size=9728 --total-num-mbufs=65536 --auto-start --stats-period 10 > $RUN/testpmd.log 2>&1"
  sleep 8; grep -a -i "port 0\|port 1\|link\|error\|fail\|start" $RUN/testpmd.log | head -12
  # DPDK numbers ports in PCI address order, not in the order given with -a.
  if [[ "$PA" < "$PB" ]]; then MAP="port 0 = $A ($PA, DU side), port 1 = $B ($PB, RU side)"; else MAP="port 0 = $B ($PB, RU side), port 1 = $A ($PA, DU side)"; fi
  echo "DPDK forwarder on cores $CORES: $MAP" | tee $RUN/tap_mode ;;
stop)
  systemctl stop fh-tap-dpdk 2>/dev/null; sleep 2
  dpdk-devbind.py -b i40e $PA $PB; sleep 3
  echo "ports returned to the kernel; rerun fh-tap.sh to rebuild the bridge"; rm -f $RUN/tap_mode ;;
status)
  systemctl is-active fh-tap-dpdk; cat $RUN/tap_mode 2>/dev/null
  grep -a -A3 "NIC statistics for port" $RUN/testpmd.log | tail -8 ;;
esac
