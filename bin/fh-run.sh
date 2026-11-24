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
    sleep 8; pgrep -x ru_emulator >/dev/null && echo "RU up; per-second counters in $RUN/ru-stdout.log" || { echo "RU exited:"; tail -20 $RUN/ru-stdout.log; exit 1; } ;;
  du)
    [ -f $RUN/du.yml ] || { echo "no $RUN/du.yml: fh-setup.sh has not finished (see $RUN/fh-setup.log)"; exit 1; }
    systemctl stop fh-du 2>/dev/null; sleep 1; : > $RUN/du.log; mkdir -p /dev/shm/cap
    systemd-run --unit=fh-du --collect -q --working-directory=/opt/fh/ocudu bash -c "sleep infinity | taskset -c $ALL /opt/fh/ocudu/build/apps/gnb/gnb -c $RUN/du.yml > $RUN/du-stdout.log 2>&1"
    sleep 20; pgrep -x gnb >/dev/null && { echo "DU up"; grep -a "N2: Connection" $RUN/du-stdout.log; } || { echo "DU exited:"; tail -20 $RUN/du-stdout.log; exit 1; }
    grep -a "OFH sector#0" $RUN/du.log | tail -1 | grep -oE "rx_total=[0-9]+ rx_early=[0-9]+ rx_on_time=[0-9]+ rx_late=[0-9]+" ;;
  stop)
    # Stopping the gNB makes it flush /dev/shm/cap/gnb_mac.pcap and gnb_ngap.pcap.
    systemctl stop fh-du fh-ru 2>/dev/null; echo stopped ;;
  status)
    echo "gnb: $(pgrep -x gnb >/dev/null && echo up || echo down); ru_emulator: $(pgrep -x ru_emulator >/dev/null && echo up || echo down)"
    [ -f $RUN/du.log ] && grep -a "OFH sector#0" $RUN/du.log | awk '{match($0,/rx_total=[0-9]+/); t+=substr($0,RSTART+9,RLENGTH-9); match($0,/rx_late=[0-9]+/); l+=substr($0,RSTART+8,RLENGTH-8); n++} END{if(n) print "DU: seconds",n,"received",t,"late",l}'
    [ -f $RUN/ru-stdout.log ] && grep -a "^| " $RUN/ru-stdout.log | awk -F"|" '{t+=$4; e+=$6; l+=$7; s+=$8; n++} END{if(n) print "RU: seconds",n,"received",t,"early",e,"late",l,"seq_err",s}'
    [ -f /opt/fh/twonode/ptp4l.log ] && tail -1 /opt/fh/twonode/ptp4l.log ;;
  *) echo "usage: sudo $0 du|ru|stop|status"; exit 2 ;;
esac
