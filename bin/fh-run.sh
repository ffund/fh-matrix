#!/bin/bash
# fh-run.sh du|ru|stop|status
# Start the OCUDU gNB (du) or ProtO-RU (ru) from the config fh-setup.sh rendered in /opt/fh/run.
# Start the RU first, so it is listening when the DU begins to send. Run with sudo.
RUN=/opt/fh/run; ALL=0-$(( $(nproc --all) - 1 ))
# The image's ProtO-RU links the distro's UHD 4.9. POWDER's radios carry FPGA images for UHD 4.10,
# so use the build against /opt/fh/uhd (UHD 4.10 from source) when the node has one.
RUBIN=/opt/fh/proto-ru/build/apps/examples/ofh/ru_emulator
[ -x /opt/fh/proto-ru/build-uhd410/apps/examples/ofh/ru_emulator ] && RUBIN=/opt/fh/proto-ru/build-uhd410/apps/examples/ofh/ru_emulator
case "${1:-}" in
  ru)
    [ -f $RUN/ru.yml ] || { echo "no $RUN/ru.yml: fh-setup.sh has not finished (see $RUN/fh-setup.log)"; exit 1; }
    systemctl stop fh-ru 2>/dev/null; sleep 1; : > $RUN/ru.log
    systemd-run --unit=fh-ru --collect -q bash -c "sleep infinity | LD_LIBRARY_PATH=/opt/fh/uhd/lib taskset -c $ALL stdbuf -oL $RUBIN -c $RUN/ru.yml > $RUN/ru-stdout.log 2>&1"
    sleep 8
    # UHD's helper threads inherit real-time priority 89 from ProtO-RU. uhd_ctrl_ep* busy-polls, so at that
    # priority it starves the kernel's RCU thread on its CPU: the kernel then reports an RCU stall every 180 s
    # and each report stalls the radio path for ~5 ms (a burst of "RF: late"). Run them as ordinary threads
    # on housekeeping CPUs instead (fh-setup.sh picks ones that do not take the NIC interrupts).
    RUPID=$(pgrep -x ru_emulator)
    for t in $(ls /proc/$RUPID/task 2>/dev/null); do
      case "$(cat /proc/$RUPID/task/$t/comm 2>/dev/null)" in
        uhd_ctrl_ep*|x300_claimer|uhd_log|action_handler|mpm_*|uhd_*) chrt -o -p 0 $t; taskset -pc $(cat $RUN/helper_cpus 2>/dev/null || echo 1,3,5) $t >/dev/null ;;
      esac
    done
    pgrep -x ru_emulator >/dev/null && echo "RU up; per-second counters in $RUN/ru-stdout.log" || { echo "RU exited:"; tail -20 $RUN/ru-stdout.log; exit 1; } ;;
  du)
    [ -f $RUN/du.yml ] || { echo "no $RUN/du.yml: fh-setup.sh has not finished (see $RUN/fh-setup.log)"; exit 1; }
    systemctl stop fh-du 2>/dev/null; sleep 1; : > $RUN/du.log; mkdir -p /dev/shm/cap
    systemd-run --unit=fh-du --collect -q --working-directory=/opt/fh/ocudu bash -c "sleep infinity | taskset -c $ALL /opt/fh/ocudu/build/apps/gnb/gnb -c $RUN/du.yml > $RUN/du-stdout.log 2>&1"
    sleep 20; if pgrep -x gnb >/dev/null; then echo "DU up"; else echo "DU exited:"; tail -20 $RUN/du-stdout.log; grep -a "\[E\]" $RUN/du.log | tail -5; exit 1; fi
    grep -a "OFH sector#0" $RUN/du.log | tail -1 | grep -oE "rx_total=[0-9]+ rx_early=[0-9]+ rx_on_time=[0-9]+ rx_late=[0-9]+" ;;
  stop)
    # Stopping the gNB makes it flush /dev/shm/cap/gnb_mac.pcap and gnb_ngap.pcap.
    systemctl stop fh-du fh-ru 2>/dev/null; echo stopped ;;
  status)
    echo "on this host: gnb $(pgrep -x gnb >/dev/null && echo up || echo down), ru_emulator $(pgrep -x ru_emulator >/dev/null && echo up || echo down); kernel RCU stalls: $(dmesg 2>/dev/null | grep -c 'self-detected stall')"
    [ -f $RUN/du.log ] && grep -a "OFH sector#0" $RUN/du.log | awk '{match($0,/rx_total=[0-9]+/); t+=substr($0,RSTART+9,RLENGTH-9); match($0,/rx_late=[0-9]+/); l+=substr($0,RSTART+8,RLENGTH-8); n++} END{if(n) print "DU: seconds",n,"received",t,"late",l}'
    [ -f $RUN/ru-stdout.log ] && grep -a "^| " $RUN/ru-stdout.log | awk -F"|" '{t+=$4; e+=$6; l+=$7; s+=$8; n++} END{if(n) print "RU: seconds",n,"received",t,"early",e,"late",l,"seq_err",s}'
    [ -f /opt/fh/twonode/ptp4l.log ] && tail -1 /opt/fh/twonode/ptp4l.log ;;
  *) echo "usage: sudo $0 du|ru|stop|status"; exit 2 ;;
esac
