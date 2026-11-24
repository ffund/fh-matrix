#!/bin/bash
# fh-setup.sh du|ru KEY=VALUE...
#
# Startup service for the DU host and the ProtO-RU host. Both run the
# fronthaul-d740-u26rt image, which carries the builds and helper scripts in /opt/fh.
# This script prepares the host, finds the fronthaul interface and the peer's MAC,
# starts PTP (DU = grandmaster, RU = slave), and renders the config for this role
# into /opt/fh/run. It does not start the gNB or ProtO-RU; see fh-run.sh.
#
# Keys (all optional, defaults below): BAND ARFCN BW PCI PLMN TAC
#   du: AMF_ADDR AMF_BIND
#   ru: RADIO (x310|n3xx) TX_GAIN RX_GAIN CLOCK (internal|external|gpsdo) USRP_ADDR
set -u
ROLE=${1:?usage: fh-setup.sh du|ru KEY=VALUE...}; shift
for kv in "$@"; do export "$kv"; done

BAND=${BAND:-78}; ARFCN=${ARFCN:-630000}; BW=${BW:-40}; PCI=${PCI:-1}; PLMN=${PLMN:-99999}; TAC=${TAC:-1}
AMF_ADDR=${AMF_ADDR:-192.168.1.1}; AMF_BIND=${AMF_BIND:-192.168.1.2}
RADIO=${RADIO:-x310}; TX_GAIN=${TX_GAIN:-30}; RX_GAIN=${RX_GAIN:-20}; CLOCK=${CLOCK:-internal}; USRP_ADDR=${USRP_ADDR:-auto}
FH_DU_IP=10.10.1.1; FH_RU_IP=10.10.1.2
REPO=/local/repository; RUN=/opt/fh/run

sudo mkdir -p $RUN /dev/shm/cap; sudo chmod a+rwx $RUN /dev/shm/cap
exec > >(tee -a $RUN/fh-setup.log) 2>&1
echo "=== fh-setup $ROLE $(date -Is) band=$BAND arfcn=$ARFCN bw=$BW pci=$PCI plmn=$PLMN tac=$TAC"

[ -x /opt/fh/ocudu/build/apps/gnb/gnb ] || { echo "!! /opt/fh is missing: this node is not running the fronthaul-d740-u26rt image"; exit 1; }
uname -r | grep -q realtime || echo "!! WARNING: not running the real-time kernel ($(uname -r))"
sudo python3 /opt/fh/scripts/smi_rate.py | tee $RUN/smi.txt
grep -q " 0.0 per second" $RUN/smi.txt || echo "!! WARNING: this node takes firmware interrupts; expect late fronthaul messages"

if [ "$ROLE" = du ]; then MY_IP=$FH_DU_IP; PEER_IP=$FH_RU_IP; else MY_IP=$FH_RU_IP; PEER_IP=$FH_DU_IP; fi
FH=""
for i in $(seq 1 60); do
  FH=$(ip -o -4 addr show | awk -v ip="$MY_IP" '{split($4,a,"/"); if (a[1]==ip) print $2}' | head -1)
  [ -n "$FH" ] && break; sleep 2
done
[ -n "$FH" ] || { echo "!! no interface has the fronthaul address $MY_IP"; exit 1; }
echo "fronthaul interface $FH ($MY_IP), peer $PEER_IP"

# Housekeeping CPUs for the NIC interrupts: the first three non-isolated CPUs on the NIC's NUMA node.
ISO=$(cat /sys/devices/system/cpu/isolated)
[ -n "$ISO" ] || { echo "!! no isolated CPUs: the image's grub isolation did not apply on this node"; exit 1; }
NIC_NUMA=$(cat /sys/class/net/$FH/device/numa_node 2>/dev/null || echo -1)
NODE_CPUS=$(cat /sys/devices/system/node/node$NIC_NUMA/cpulist 2>/dev/null || echo "")
HK=$(python3 - "$ISO" "$NODE_CPUS" <<'PY'
import os, sys
def cl(s):
    o = []
    for p in s.split(','):
        if p.strip():
            a, _, b = p.partition('-'); o += range(int(a), int(b or a) + 1)
    return o
iso = set(cl(sys.argv[1])); local = cl(sys.argv[2]) or list(range(os.cpu_count()))
print(','.join([str(c) for c in local if c not in iso][:3]))
PY
)
sudo /opt/fh/scripts/host_prep.sh "$FH" "$HK"
echo "fronthaul NIC NUMA node: $NIC_NUMA; isolated cores: $ISO; NIC interrupts on: $HK"
python3 - "$ISO" "$NODE_CPUS" <<'PY'
import sys
def cl(s):
    o = []
    for p in s.split(','):
        if p.strip():
            a, _, b = p.partition('-'); o += range(int(a), int(b or a) + 1)
    return o
iso, local = cl(sys.argv[1]), cl(sys.argv[2])
if local and not set(iso) & set(local):
    print("!! WARNING: none of the isolated cores are on the fronthaul NIC's NUMA node; re-derive the grub isolation for this host")
PY

# Peer MAC over the fronthaul link (the peer may still be booting).
PEER_MAC=""
for i in $(seq 1 450); do      # up to 15 minutes: the other host loads the same image
  ping -c 1 -W 1 -I "$FH" "$PEER_IP" >/dev/null 2>&1
  PEER_MAC=$(ip neigh show "$PEER_IP" dev "$FH" | awk '/lladdr/{print $3}' | head -1)
  [ -n "$PEER_MAC" ] && break; sleep 2
done
[ -n "$PEER_MAC" ] || { echo "!! could not learn the MAC of $PEER_IP on $FH"; exit 1; }
MY_MAC=$(cat /sys/class/net/$FH/address)
echo "MACs: mine $MY_MAC, peer $PEER_MAC"
ping -c 2 -W 1 -M do -s 8972 -I "$FH" "$PEER_IP" >/dev/null 2>&1 && echo "jumbo frames pass" || echo "!! WARNING: an 8972-byte ping to the peer failed; full-size fronthaul frames need an MTU above 3000"

# PTP: the image's start_ptp.sh expects its configs in /opt/fh/twonode.
sudo cp $REPO/etc/fh/ptp-gm.cfg $REPO/etc/fh/ptp-slave.cfg /opt/fh/twonode/
# Ubuntu 26.04 confines ptp4l with AppArmor to configs under /etc/linuxptp; allow ours too.
if [ -f /etc/apparmor.d/usr.sbin.ptp4l ]; then
  echo "/opt/fh/twonode/*.cfg r," | sudo tee /etc/apparmor.d/local/usr.sbin.ptp4l >/dev/null
  sudo apparmor_parser -r /etc/apparmor.d/usr.sbin.ptp4l
fi
# The phc2sys profile lacks write access to ptp4l's socket, so "phc2sys -w" waits forever.
if [ -f /etc/apparmor.d/usr.sbin.phc2sys ]; then
  # (that profile has no local include, so add the rule to the profile itself)
  grep -q '^  /run/ptp4l rw,' /etc/apparmor.d/usr.sbin.phc2sys || sudo sed -i '$ s|^}|  /run/ptp4l rw,\n}|' /etc/apparmor.d/usr.sbin.phc2sys
  sudo apparmor_parser -r /etc/apparmor.d/usr.sbin.phc2sys
fi
if [ "$ROLE" = du ]; then sudo /opt/fh/twonode/start_ptp.sh gm "$FH"; else sudo /opt/fh/twonode/start_ptp.sh slave "$FH"; fi

DEVARGS=""; SRATE=""
if [ "$ROLE" = ru ]; then
  # The radio hangs off a second experiment interface. Its address differs per radio
  # (192.168.10.2, .20.2, .30.2 or .40.2), so cover each subnet and ask UHD.
  RIF=$(ip -o -4 addr show | awk '$4 ~ /^192\.168\.(10|20|30|40)\.1\// {print $2}' | head -1)
  [ -n "$RIF" ] || { echo "!! no interface for the radio link (expected 192.168.{10,20,30,40}.1)"; exit 1; }
  sudo ip link set "$RIF" mtu 9000 up
  for n in 10 20 30 40; do sudo ip addr add 192.168.$n.1/24 dev "$RIF" 2>/dev/null; done
  sudo sysctl -qw net.core.rmem_max=33554432 net.core.wmem_max=33554432
  if [ "$USRP_ADDR" = auto ]; then
    for i in $(seq 1 30); do
      USRP_ADDR=$(uhd_find_devices 2>/dev/null | awk '/addr:/{print $2}' | grep -E '^192\.168\.' | head -1)
      [ -n "$USRP_ADDR" ] && break; USRP_ADDR=auto; sleep 5
    done
  fi
  [ "$USRP_ADDR" != auto ] || { echo "!! UHD found no radio on $RIF; check that the SDR is powered and its FPGA image matches UHD $(uhd_config_info --version)"; uhd_find_devices; exit 1; }
  uhd_find_devices --args "addr=$USRP_ADDR" | tee $RUN/uhd_find.txt
  case "$RADIO" in
    x310) DEVARGS="type=x300,addr=$USRP_ADDR,master_clock_rate=184.32e6"
          case "$BW" in 20) SRATE=23.04;; *) SRATE=61.44;; esac ;;
    n3xx) DEVARGS="type=n3xx,addr=$USRP_ADDR,master_clock_rate=122.88e6,recv_frame_size=7900,send_frame_size=7900"
          case "$BW" in 20) SRATE=30.72;; *) SRATE=61.44;; esac ;;
    *) echo "!! unknown RADIO=$RADIO"; exit 1 ;;
  esac
  echo "radio $RADIO at $USRP_ADDR via $RIF: $DEVARGS, $SRATE Msps"
fi

export ROLE FH MY_MAC PEER_MAC ISO BAND ARFCN BW PCI PLMN TAC AMF_ADDR AMF_BIND DEVARGS SRATE TX_GAIN RX_GAIN CLOCK RUN
python3 - <<'PY'
import os
E = os.environ
def cl(s):
    o = []
    for p in s.split(','):
        a, _, b = p.partition('-'); o += range(int(a), int(b or a) + 1)
    return o
def primary(c):
    # True for the first hardware thread of a core, so each real-time thread gets a whole core.
    try:
        sib = cl(open('/sys/devices/system/cpu/cpu%d/topology/thread_siblings_list' % c).read().strip())
        return c == min(sib)
    except (OSError, ValueError):
        return True
iso = [c for c in cl(E['ISO']) if primary(c)]
role = E['ROLE']
v = dict(HOST=os.uname().nodename, FH=E['FH'], MY_MAC=E['MY_MAC'], PEER_MAC=E['PEER_MAC'], ISO=str(iso),
         BAND=E['BAND'], ARFCN=E['ARFCN'], BW=E['BW'], PCI=E['PCI'], PLMN=E['PLMN'], TAC=E['TAC'], RUN=E['RUN'])
if role == 'du':
    if len(iso) < 4: raise SystemExit('!! need 4 isolated cores for the DU, have %s' % iso)
    ru, ofh = iso[0:2], iso[2:4]
    # As on COSMOS: the upper-PHY pool is every other core except CPU 0.
    pool = [c for c in range(1, os.cpu_count()) if primary(c) and c not in ru + ofh]
    v.update(AMF_ADDR=E['AMF_ADDR'], AMF_BIND=E['AMF_BIND'], RUCPUS=','.join(map(str, ru)), OFH=','.join(map(str, ofh)),
             POOL=','.join(map(str, pool)), NTHR=str(min(16, len(pool))))
else:
    if len(iso) < 7: raise SystemExit('!! need 7 isolated cores for ProtO-RU, have %s' % iso)
    v.update(TIMING=str(iso[0]), OFH=', '.join(map(str, iso[1:4])), RUCPUS=', '.join(map(str, iso[4:7])),
             DEVARGS=E['DEVARGS'], SRATE=E['SRATE'], TX_GAIN=E['TX_GAIN'], RX_GAIN=E['RX_GAIN'], CLOCK=E['CLOCK'])
t = open('/local/repository/etc/fh/%s.yml.tmpl' % role).read()
for k, val in v.items():
    t = t.replace('@%s@' % k, val)
left = [w for w in t.split() if w.startswith('@') and w.endswith('@')]
if left: raise SystemExit('!! unfilled placeholders: %s' % left)
out = '%s/%s.yml' % (E['RUN'], role)
open(out, 'w').write(t); print('wrote', out)
PY
[ $? -eq 0 ] || exit 1
echo "=== fh-setup $ROLE done. Start with: sudo $REPO/bin/fh-run.sh $ROLE"
