#!/bin/bash
# fh-capture.sh <name> [seconds]   Run with sudo on the DU host.
# Full-frame fronthaul capture into /dev/shm/cap/<name>/ with the DU's counters before and after,
# as in the COSMOS procedure. Stop the gNB afterwards (fh-run.sh stop) to flush its MAC/NGAP pcaps.
NAME=${1:?usage: fh-capture.sh <name> [seconds]}; SECS=${2:-10}; RUN=/opt/fh/run
FH=$(awk '/network_interface:/{print $3; exit}' $RUN/du.yml)
CAP=/dev/shm/cap/$NAME; mkdir -p $CAP
grep -a "OFH sector#0" $RUN/du.log | tail -1 | cut -c1-220 > $CAP/du_ofh_before.txt
date -Ins > $CAP/start_time.txt
# -p: leave promiscuous mode alone (the DU needs it on). Core 0 is not isolated.
taskset -c 0 timeout -s INT "$SECS" tcpdump -p -i "$FH" -s 0 -B 524288 --time-stamp-precision=nano -Z root \
  -w $CAP/fh.pcap ether proto 0xaefe 2> $CAP/fh.stats
date -Ins > $CAP/stop_time.txt; sleep 2
grep -a "OFH sector#0" $RUN/du.log | tail -$((SECS + 2)) | cut -c1-220 > $CAP/du_ofh_after.txt
cp $RUN/du.yml $CAP/
tail -3 $CAP/fh.stats; python3 /opt/fh/scripts/pcap_census.py $CAP/fh.pcap; ls -la $CAP
