#!/bin/bash
# fh-core-local.sh   Startup service on the DU host when the profile has no separate core node.
# Starts the Open5GS build that ships in the image (/opt/open5gs) on this host, reconfigured for POWDER's test
# SIMs: PLMN 999/99, TAC 1, slice SST 1 / SD 000001, UE subnet 10.45.0.0/16 (gateway 10.45.0.1, as on POWDER's
# packaged core), and the two subscribers that PowderTeam's deploy-open5gs.sh provisions. The AMF listens on
# 127.0.0.5; fh-setup.sh is then run with AMF_ADDR=127.0.0.5 AMF_BIND=127.0.0.1.
set -u
RUN=/opt/fh/run; sudo mkdir -p $RUN; sudo chmod a+rwx $RUN
exec > >(tee -a $RUN/fh-core.log) 2>&1
echo "=== fh-core-local $(date -Is)"
E=/opt/open5gs/etc
sudo sed -i -E 's/^(\s*)mcc: *0*01$/\1mcc: 999/; s/^(\s*)mnc: *0*1$/\1mnc: 99/; s/^(\s*)mnc: *01$/\1mnc: 99/; s/^(\s*)tac: *7$/\1tac: 1/' $E/amf.yaml $E/nrf.yaml
grep -q "sd: 000001" $E/amf.yaml || sudo sed -i -E 's/^(\s*)- sst: 1$/\1- sst: 1\n\1  sd: 000001/' $E/amf.yaml
sudo sed -i 's#192\.168\.100\.0/22#10.45.0.0/16#g; s#192\.168\.100\.1#10.45.0.1#g' $E/upf.yaml $E/smf.yaml
sudo sed -i 's#^UE_SUBNET=.*#UE_SUBNET=10.45.0.1/16; UE_NET=10.45.0.0/16#' /opt/fh/core/start.sh
grep -n "mcc\|mnc\|tac:\|sst\|sd:" $E/amf.yaml | head -12
sudo /opt/fh/core/start.sh /nonexistent | tail -15
# Subscribers: POWDER's throwaway test SIMs (same values as bin/deploy-open5gs.sh), slice 1/000001, DNN internet.
sudo python3 - <<'PY'
import pymongo
db = pymongo.MongoClient("mongodb://127.0.0.1/open5gs").open5gs
for imsi in ("999990000000118", "999990000000141"):
    sess = {"name": "internet", "type": 1, "pcc_rule": [],
            "ambr": {"uplink": {"value": 1, "unit": 3}, "downlink": {"value": 1, "unit": 3}},
            "qos": {"index": 9, "arp": {"priority_level": 8, "pre_emption_capability": 1, "pre_emption_vulnerability": 1}}}
    sub = {"imsi": imsi, "subscribed_rau_tau_timer": 12, "network_access_mode": 0, "subscriber_status": 0,
           "access_restriction_data": 32, "slice": [{"sst": 1, "sd": "000001", "default_indicator": True, "session": [sess]}],
           "ambr": {"uplink": {"value": 1, "unit": 3}, "downlink": {"value": 1, "unit": 3}},
           "security": {"k": "00112233445566778899aabbccddeeff", "amf": "8000", "op": None,
                        "opc": "0ed47545168eafe2c39c075829a7b61f"}, "schema_version": 1, "__v": 0}
    db.subscribers.replace_one({"imsi": imsi}, sub, upsert=True); print("  provisioned", imsi)
PY
command -v iperf3 >/dev/null && (pgrep -f "iperf3 -s" >/dev/null || { iperf3 -s -D; iperf3 -s -D -p 5202; })
echo "=== fh-core-local done: AMF on 127.0.0.5, UEs on 10.45.0.0/16, iperf3 servers on 10.45.0.1:5201/5202"
