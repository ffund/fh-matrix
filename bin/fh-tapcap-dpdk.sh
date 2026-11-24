#!/bin/bash
# fh-tapcap-dpdk.sh <name> [seconds]   Run as root on the tap node while fh-tap-dpdk.sh is forwarding.
# Captures one of the two forwarded ports in both directions (received and sent), so every fronthaul frame appears
# exactly once; the forwarder hands dumpcap a copy through a ring and keeps forwarding. Output: /dev/shm/cap/<name>/fh.pcap
NAME=${1:?usage: fh-tapcap-dpdk.sh <name> [seconds]}; SECS=${2:-10}
CAP=/dev/shm/cap/$NAME; mkdir -p $CAP; date -Ins > $CAP/start_time.txt
dpdk-dumpcap --file-prefix=fhtap --lcore=$(( $(nproc) - 2 )) -i 0 -s 9600 -P -a duration:$SECS -w $CAP/fh.pcap > $CAP/dumpcap.log 2>&1
date -Ins > $CAP/stop_time.txt
tail -3 $CAP/dumpcap.log; ls -la $CAP/fh.pcap | awk '{print $5, $9}'
