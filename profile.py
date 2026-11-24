#!/usr/bin/env python

"""O-RAN 7.2 fronthaul capture rig on the POWDER programmable attenuator matrix.

Derived from PowderTeam/srs-rf-matrix (gitlab.flux.utah.edu/dmaas/srs-rf-matrix, commit
06a89b0). That profile runs an OCUDU gNB that drives the X310 directly. This one splits
the gNB at 7.2: an OCUDU O-DU on one host, ProtO-RU on a second host driving the radio,
and an Ethernet fronthaul between them that you can capture.
"""

import os

import geni.portal as portal
import geni.rspec.pg as pg
import geni.rspec.igext as ig
import geni.rspec.emulab.pnext as pn
import geni.rspec.emulab as emulab


tourDescription = """
### O-RAN 7.2 fronthaul capture: OCUDU O-DU + ProtO-RU + a DPDK tap, on the attenuator matrix

A conducted 5G cell split at O-RAN 7.2, with a commercial UE, programmable path loss, and a
tap node that forwards and records the fronthaul between the DU and the radio unit.

Deployed (defaults):

- `du`: OCUDU O-DU and CU (d740, image `fronthaul-d740-u26rt-uhd410`).
- `ruhost`: ProtO-RU software O-RU (d740, same image), driving the radio.
- `tap` (d740, same image): forwards every fronthaul frame between `du` and `ruhost` with DPDK
  and records them with `dpdk-dumpcap`, so neither end captures. Its clock follows PTP.
- `cn5g`: Open5GS core (d430), as PowderTeam/srs-rf-matrix installs it. With `core_on_du`
  the image's own Open5GS runs on `du` instead and no `cn5g` node is allocated.
- `ru-sdr`: the radio on the matrix, X310 `x310-1` or N300 `n300-2`.
- `ue1` (and optionally `ue2`): Intel NUC with a Quectel RM520N-GL.

PTP (DU = grandmaster) runs on a separate `ptp-link` as unicast UDP, because Layer-2 PTP
multicast does not cross POWDER's racks. Turn `use_tap` off for the older layout: one
`fh-link` between `du` and `ruhost`, PTP on it, and captures taken on `du`.
"""

tourInstructions = """
Startup scripts are still running when the experiment becomes ready. Wait until the
"Startup" column shows "Finished" for every compute node. A host whose fronthaul NIC sits on
the other CPU socket re-isolates its cores and reboots once by itself.

**1. Check the setup.**

```
tail -5 /opt/fh/run/fh-setup.log     # on du and ruhost: ends with "fh-setup <role> done"
tail -5 /opt/fh/run/fh-tap.log       # on tap: ends with "fh-tap done (DPDK)"
tail -2 /opt/fh/twonode/ptp4l.log    # on ruhost and tap: "rms" lines of a few ns
```

**2. Start the O-RU, then the O-DU.**

```
sudo /local/repository/bin/fh-run.sh ru       # on ruhost
sudo /local/repository/bin/fh-run.sh du       # on du
sudo /local/repository/bin/fh-run.sh status   # either host: late counts, RCU stalls
sudo /local/repository/bin/fh-tap-dpdk.sh status   # on tap: forwarder counters (RX-missed must stay 0)
```

**3. Attach the UE.** On `ue1`: `sudo quectel-CM -s internet -4 &` then
`/local/repository/bin/module-on.sh`. It gets an address in 10.45.0.0/16.

**4. Set the path loss.** On `du`: `bin/update-attens ru1ue1 <0..95>` (and `ru1ue2` for
`ue2`); the real attenuation is 30 dB plus the value. `bin/atten -l` lists the paths.

**5. Capture.** Start traffic (`iperf3 -c 10.45.0.1 -R` on the UE), then on `tap`:

```
sudo /local/repository/bin/fh-tapcap-dpdk.sh run1 10    # /dev/shm/cap/run1/fh.pcap, both directions
```

Stopping the gNB (`fh-run.sh stop` on `du`) writes its MAC and NGAP pcaps to `/dev/shm/cap`
on `du`. Captures are in RAM; copy them off before the experiment ends.

**Known limits**

- The tap must not be a d430: its firmware interrupts freeze every core, DPDK included.
- ProtO-RU supports PRACH format B4 and single-section C-plane messages only.
"""

BIN_PATH = "/local/repository/bin"
UBUNTU_IMG = "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU22-64-STD"
FH_IMG = "urn:publicid:IDN+emulab.net+image+nyunetworks//fronthaul-d740-u26rt-uhd410"
COTS_UE_IMG = "urn:publicid:IDN+emulab.net+image+PowderTeam:cots-jammy-image"
COMP_MANAGER_ID = "urn:publicid:IDN+emulab.net+authority+cm"
# Unmodified from PowderTeam/srs-rf-matrix. It installs Open5GS and provisions the POWDER test SIMs.
OPEN5GS_DEPLOY_SCRIPT = os.path.join(BIN_PATH, "deploy-open5gs.sh")
FH_SETUP_SCRIPT = os.path.join(BIN_PATH, "fh-setup.sh")

# Matrix wiring, from srs-rf-matrix. Both radios reach both UE NUCs.
UE_NODE_IDS = {"ue1": "nuc27", "ue2": "nuc22"}
RADIOS = {
    # radio id: (UHD type for fh-setup.sh, {ue: {attenuator group: start value}})
    # The X310's channel A is "ru1" in srs-rf-matrix's group names and channel B is "ru2".
    # ProtO-RU runs one antenna on channel A, so open ru1 and close ru2.
    "x310-1": ("x310", {"ue1": {"ru1ue1": 0, "ru2ue1": 95}, "ue2": {"ru1ue2": 0, "ru2ue2": 95}}),
    # srs-rf-matrix has one group for the N300, covering its paths to both UEs.
    "n300-2": ("n3xx", {"ue1": {"uemon": 0}, "ue2": {"uemon": 0}}),
}

pc = portal.Context()
node_types = [
    ("d430", "Emulab, d430"),
    ("d740", "Emulab, d740"),
    ("d760p", "Emulab, d760"),
    ("d760-gpu", "Emulab, d760 w/ GPU"),
]
pc.defineParameter(
    name="radio",
    description="Radio on the matrix that ProtO-RU drives",
    typ=portal.ParameterType.STRING,
    defaultValue="x310-1",
    legalValues=[("x310-1", "X310 (x310-1)"), ("n300-2", "N300 (n300-2), sibling of the N310 ProtO-RU supports")],
)
pc.defineParameter(
    name="include_ue2",
    description="Also allocate the second COTS UE (nuc22)",
    typ=portal.ParameterType.BOOLEAN,
    defaultValue=False,
)
pc.defineParameter(
    name="bandwidth",
    description="Cell bandwidth in MHz",
    typ=portal.ParameterType.INTEGER,
    defaultValue=40,
    legalValues=[(40, "40 MHz (106 PRB, as on COSMOS)"), (20, "20 MHz (51 PRB, as in srs-rf-matrix)")],
)
pc.defineParameter(
    name="dl_arfcn",
    description="Downlink NR-ARFCN (band n78)",
    typ=portal.ParameterType.INTEGER,
    defaultValue=632628,
    longDescription="632628 is 3489.42 MHz, the carrier srs-rf-matrix uses on the matrix.",
)
pc.defineParameter(
    name="tx_gain",
    description="Radio TX gain (ProtO-RU subtracts a 12 dB backoff)",
    typ=portal.ParameterType.INTEGER,
    defaultValue=30,
    advanced=True,
)
pc.defineParameter(
    name="rx_gain",
    description="Radio RX gain",
    typ=portal.ParameterType.INTEGER,
    defaultValue=20,
    advanced=True,
)
pc.defineParameter(
    name="clock",
    description="USRP clock and time source",
    typ=portal.ParameterType.STRING,
    defaultValue="internal",
    legalValues=[("internal", "internal"), ("external", "external 10 MHz / PPS")],
    longDescription="ProtO-RU drifts out of the timing window on long runs with the internal clock.",
    advanced=True,
)
pc.defineParameter(
    name="fh_nodetype",
    description="Type of compute node for the O-DU and the ProtO-RU host",
    typ=portal.ParameterType.STRING,
    defaultValue=node_types[1],
    legalValues=node_types,
    longDescription="The image was built and tested on a d740 only. A d430 takes one firmware "
                    "interrupt per second and cannot host either end.",
    advanced=True,
)
pc.defineParameter(
    name="cn_nodetype",
    description="Type of compute node to use for CN node",
    typ=portal.ParameterType.STRING,
    defaultValue=node_types[0],
    legalValues=node_types,
    advanced=True,
)
pc.defineParameter(
    name="use_tap",
    description="Put a tap node between the DU and the RU host that forwards and records the fronthaul",
    typ=portal.ParameterType.BOOLEAN,
    defaultValue=True,
    longDescription="Default. The tap forwards with DPDK and records with dpdk-dumpcap (bin/fh-tapcap-dpdk.sh), so "
                    "neither end runs a capture. Without it, the DU and RU host share one link and you capture on "
                    "the DU (bin/fh-capture.sh), which delays a few downlink messages.",
)
pc.defineParameter(
    name="tap_mode",
    description="How the tap forwards",
    typ=portal.ParameterType.STRING,
    defaultValue="dpdk",
    legalValues=[("dpdk", "DPDK (dpdk-testpmd + dpdk-dumpcap)"), ("bridge", "Linux kernel bridge + tcpdump")],
)
pc.defineParameter(
    name="core_on_du",
    description="Run the 5G core on the DU host instead of a separate node",
    typ=portal.ParameterType.BOOLEAN,
    defaultValue=False,
    longDescription="Uses the Open5GS build in the image (bin/fh-core-local.sh), saving a node.",
)
pc.defineParameter(
    name="tap_nodetype",
    description="Type of compute node for the tap that bridges and records the fronthaul",
    typ=portal.ParameterType.STRING,
    defaultValue=node_types[1],
    legalValues=node_types,
    longDescription="Use a d740. A d430 takes firmware interrupts (1 to 2 per second) that freeze every core, "
                    "DPDK polling cores included, so the RU sees late downlink messages every second.",
)
pc.defineParameter(
    name="fh_image",
    description="Disk image for the O-DU and ProtO-RU hosts",
    typ=portal.ParameterType.STRING,
    defaultValue=FH_IMG,
    advanced=True,
)

params = pc.bindParameters()
pc.verifyParameters()
request = pc.makeRequestRSpec()

radio_type, radio_atten = RADIOS[params.radio]
cell_args = "BAND=78 ARFCN={} BW={} PCI=1 PLMN=99999 TAC=1".format(params.dl_arfcn, params.bandwidth)

# Core network, as in srs-rf-matrix, unless it runs on the DU host.
if not params.core_on_du:
    role = "cn5g"
    cn_node = request.RawPC(role)
    cn_node.component_manager_id = COMP_MANAGER_ID
    cn_node.hardware_type = params.cn_nodetype
    cn_node.disk_image = UBUNTU_IMG
    cn_if = cn_node.addInterface("{}-if".format(role))
    cn_if.addAddress(pg.IPv4Address("192.168.1.1", "255.255.255.0"))
    cn_link = request.Link("{}-link".format(role))
    cn_link.setNoBandwidthShaping()
    cn_link.addInterface(cn_if)
    cn_node.addService(pg.Execute(shell="bash", command=OPEN5GS_DEPLOY_SCRIPT))

# The fronthaul: one link between the O-DU and the ProtO-RU host. fh-setup.sh finds its
# interface by these addresses. The frames on it are raw Ethernet (eCPRI), untagged.
# Tap variant: the fronthaul is two links with a tap node bridging them (bin/fh-tap.sh), and PTP has its own
# direct DU-to-RU link so the software bridge does not touch the timestamps.
if params.use_tap:
    # DU -- tap -- RU host on two links; PTP on its own direct link (unicast UDP), with the tap as a second slave.
    fh_link = request.Link("fh-du-tap")
    fh_link2 = request.Link("fh-tap-ru")
    fh_link2.setNoBandwidthShaping()
    ptp_link = request.Link("ptp-link")
    ptp_link.setNoBandwidthShaping()
    tap = request.RawPC("tap")
    tap.component_manager_id = COMP_MANAGER_ID
    tap.hardware_type = params.tap_nodetype
    tap.disk_image = params.fh_image
    tap_du_if = tap.addInterface("tap-du-if")
    tap_du_if.addAddress(pg.IPv4Address("10.10.1.11", "255.255.255.0"))
    fh_link.addInterface(tap_du_if)
    tap_ru_if = tap.addInterface("tap-ru-if")
    tap_ru_if.addAddress(pg.IPv4Address("10.10.1.12", "255.255.255.0"))
    fh_link2.addInterface(tap_ru_if)
    tap_ptp_if = tap.addInterface("tap-ptp-if")
    tap_ptp_if.addAddress(pg.IPv4Address("10.10.2.3", "255.255.255.0"))
    ptp_link.addInterface(tap_ptp_if)
    tap.addService(pg.Execute(shell="bash", command="{} TAP_MODE={}".format(os.path.join(BIN_PATH, "fh-tap.sh"), params.tap_mode)))
else:
    # One link between the DU and the RU host; PTP runs on it (DU = grandmaster).
    fh_link = request.Link("fh-link")
    fh_link2 = fh_link
fh_link.setNoBandwidthShaping()

node_name = "du"
du = request.RawPC(node_name)
du.component_manager_id = COMP_MANAGER_ID
du.hardware_type = params.fh_nodetype
du.disk_image = params.fh_image
if not params.core_on_du:
    du_cn_if = du.addInterface("{}-cn-if".format(node_name))
    du_cn_if.addAddress(pg.IPv4Address("192.168.1.2", "255.255.255.0"))
    cn_link.addInterface(du_cn_if)
    amf_args = "AMF_ADDR=192.168.1.1 AMF_BIND=192.168.1.2"
else:
    du.addService(pg.Execute(shell="bash", command=os.path.join(BIN_PATH, "fh-core-local.sh")))
    amf_args = "AMF_ADDR=127.0.0.5 AMF_BIND=127.0.0.1"
du_fh_if = du.addInterface("{}-fh-if".format(node_name))
du_fh_if.addAddress(pg.IPv4Address("10.10.1.1", "255.255.255.0"))
fh_link.addInterface(du_fh_if)
if params.use_tap:
    du_ptp_if = du.addInterface("du-ptp-if")
    du_ptp_if.addAddress(pg.IPv4Address("10.10.2.1", "255.255.255.0"))
    ptp_link.addInterface(du_ptp_if)
du.addService(pg.Execute(shell="bash", command="{} du {} {}".format(FH_SETUP_SCRIPT, cell_args, amf_args)))
# Attenuator control works from the compute nodes, not the NUCs. Set the start values for
# the paths between the chosen radio and the allocated UEs (real attenuation is 30 + value dB).
atten_start = {}
for ue_name in (["ue1", "ue2"] if params.include_ue2 else ["ue1"]):
    atten_start.update(radio_atten[ue_name])
for group in sorted(atten_start):
    du.addService(pg.Execute(shell="bash", command="/local/repository/bin/update-attens {} {}".format(group, atten_start[group])))

node_name = "ruhost"
ruhost = request.RawPC(node_name)
ruhost.component_manager_id = COMP_MANAGER_ID
ruhost.hardware_type = params.fh_nodetype
ruhost.disk_image = params.fh_image
ru_fh_if = ruhost.addInterface("{}-fh-if".format(node_name))
ru_fh_if.addAddress(pg.IPv4Address("10.10.1.2", "255.255.255.0"))
fh_link2.addInterface(ru_fh_if)
if params.use_tap:
    ru_ptp_if = ruhost.addInterface("ruhost-ptp-if")
    ru_ptp_if.addAddress(pg.IPv4Address("10.10.2.2", "255.255.255.0"))
    ptp_link.addInterface(ru_ptp_if)
node_sdr_if = ruhost.addInterface("usrp_if")
node_sdr_if.addAddress(pg.IPv4Address("192.168.30.1", "255.255.255.0"))
sdr_link = request.Link("{}-sdr-link".format(node_name))
sdr_link.addInterface(node_sdr_if)
ruhost.addService(pg.Execute(shell="bash", command="{} ru {} RADIO={} TX_GAIN={} RX_GAIN={} CLOCK={}".format(
    FH_SETUP_SCRIPT, cell_args, radio_type, params.tx_gain, params.rx_gain, params.clock)))

# collect node objects for RF matrix
matrix_nodes = {}

node_name = "ru"
ru_sdr = request.RawPC("{}-sdr".format(node_name))
ru_sdr.component_id = params.radio
sdr_link.addNode(ru_sdr)
ru_sdr.Desire("rf-controlled", 1)
matrix_nodes[node_name] = ru_sdr

# ue nodes with COTS UE and B210
ue_names = ["ue1", "ue2"] if params.include_ue2 else ["ue1"]
for node_name in ue_names:
    ue = request.RawPC(node_name)
    ue.component_manager_id = COMP_MANAGER_ID
    ue.component_id = UE_NODE_IDS[node_name]
    ue.disk_image = COTS_UE_IMG
    ue.Desire("rf-controlled", 1)
    ue.addService(pg.Execute(shell="bash", command="/local/repository/bin/module-airplane.sh"))
    ue.addService(pg.Execute(shell="bash", command="/local/repository/bin/setup-cots-ue.sh internet"))
    matrix_nodes[node_name] = ue

# One RF link per radio/UE pair through the attenuator matrix.
for ue_name in ue_names:
    ru_rf_if = matrix_nodes["ru"].addInterface("ru_{}_rf".format(ue_name))
    ue_rf_if = matrix_nodes[ue_name].addInterface("{}_ru_rf".format(ue_name))
    rf_link = request.RFLink("rflink_ru_{}".format(ue_name))
    rf_link.addInterface(ru_rf_if)
    rf_link.addInterface(ue_rf_if)

tour = ig.Tour()
tour.Description(ig.Tour.MARKDOWN, tourDescription)
tour.Instructions(ig.Tour.MARKDOWN, tourInstructions)
request.addTour(tour)

pc.printRequestRSpec(request)
