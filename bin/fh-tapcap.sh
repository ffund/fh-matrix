#!/bin/bash
# fh-tapcap.sh <name> [seconds]   Run with sudo on the tap node.
# Records the fronthaul in both directions into /dev/shm/cap/<name>/: dl.pcap holds frames arriving from the
# DU, ul.pcap frames arriving from the RU host. Each is captured on the port where it ARRIVES (-Q in), so the
# capture is never in the path of a frame being sent. Nanosecond timestamps from this node's clock.
# Merge afterwards with: mergecap -w fh.pcap dl.pcap ul.pcap
NAME=${1:?usage: fh-tapcap.sh <name> [seconds]}; SECS=${2:-10}; RUN=/opt/fh/run
A=$(cat $RUN/tap_du_if); B=$(cat $RUN/tap_ru_if); CAP=/dev/shm/cap/$NAME; mkdir -p $CAP
date -Ins > $CAP/start_time.txt
taskset -c 2 timeout -s INT "$SECS" tcpdump -p -Q in -i "$A" -s 0 -B 524288 --time-stamp-precision=nano -Z root -w $CAP/dl.pcap ether proto 0xaefe 2> $CAP/dl.stats &
taskset -c 4 timeout -s INT "$SECS" tcpdump -p -Q in -i "$B" -s 0 -B 524288 --time-stamp-precision=nano -Z root -w $CAP/ul.pcap ether proto 0xaefe 2> $CAP/ul.stats &
wait; date -Ins > $CAP/stop_time.txt
for d in dl ul; do echo "$d: $(grep -a 'packets captured\|dropped by kernel' $CAP/$d.stats | tr '\n' ' ')"; done
ls -la $CAP | awk '{print $5, $9}' | tail -6
