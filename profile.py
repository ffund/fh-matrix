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
### O-RAN 7.2 fronthaul capture through a tap node: OCUDU O-DU + ProtO-RU on the attenuator matrix

**Branch `tap`.** A third node (`tap`) bridges the fronthaul between `du` and `ruhost` and records it with
`sudo /local/repository/bin/fh-tapcap.sh <name> [seconds]`, so neither end captures. PTP runs on a separate
direct link.

A conducted 5G cell split at O-RAN 7.2, for capturing the DU-to-RU fronthaul with a
commercial UE and programmable path loss. **Untested as a whole**: each part ran
separately (see the notes in the instructions).

Deployed:

- `cn5g`: Open5GS core (Dell R430), installed the way PowderTeam/srs-rf-matrix does it.
- `du`: OCUDU O-DU and CU (Dell R740, image `fronthaul-d740-u26rt-uhd410`).
- `ruhost`: ProtO-RU software O-RU (Dell R740, same image), driving the radio.
- `ru-sdr`: the radio on the matrix, X310 `x310-1` or N300 `n300-2`.
- `ue1` (and optionally `ue2`): Intel NUC with a Quectel RM520 COTS UE.

The fronthaul is the `fh-link` between `du` and `ruhost`. It runs untagged, with PTP
from `du` (grandmaster) to `ruhost`.
"""

tourInstructions = """
Startup scripts are still running when the experiment becomes ready. Wait until the
"Startup" column shows "Finished" for every compute node.

**1. Check the setup on both hosts.** `fh-setup.sh` prepared the host, started PTP and
rendered a config. Its log ends with `fh-setup <role> done` on success:

```
# on du and on ruhost
tail -5 /opt/fh/run/fh-setup.log
```

On `ruhost` the log also shows which radio address UHD found. If UHD reports an FPGA
or RFNoC compatibility mismatch, the radio carries an image for another UHD release
than the 4.9 in the image. For the X310:

```
# on ruhost
sudo uhd_images_downloader -t x3xx
uhd_image_loader --args="type=x300,addr=<radio address>,fpga=XG"
# power cycle ru-sdr from the portal list view, wait a minute, then rerun:
sudo /local/repository/bin/fh-setup.sh ru <same KEY=VALUE arguments as in the startup log>
```

**2. Start the O-RU, then the O-DU.**

```
# on ruhost
sudo /local/repository/bin/fh-run.sh ru
# on du
sudo /local/repository/bin/fh-run.sh du
```

The DU prints `N2: Connection to AMF ... completed` and a fronthaul counter line.
`rx_on_time` must equal `rx_total`, with `rx_late=0`.
`sudo /local/repository/bin/fh-run.sh status` on either host summarises the counters.

**3. Attach the UE.**

```
# on ue1
sudo quectel-CM -s internet -4          # in one session
/local/repository/bin/module-on.sh      # in another
```

Watch the attach on `cn5g`: `sudo journalctl -u open5gs-amfd -u open5gs-smfd -f --output cat`.
The UE scripts from srs-rf-matrix are in `/local/repository/bin` (`ue_metrics.py`,
`ue_app.py`, `quectel_control.py`, `ue-services`).

**4. Set the path loss.** Run on `du`. `bin/atten -l` lists the paths this experiment
controls. `bin/update-attens <group> <0..95>` sets a group; the real attenuation is
30 dB plus the value. With the X310 the groups are `ru1ue1` and `ru1ue2` (channel A to
each UE; the profile opens these and closes channel B). With the N300 the one group is
`uemon` (both UEs together).

**5. Capture.** Traffic first (`iperf3 -s` on `cn5g`, `iperf3 -c 10.45.0.1` on the UE), then:

```
# on du
sudo /local/repository/bin/fh-capture.sh run1 10
sudo /local/repository/bin/fh-run.sh stop     # flushes /dev/shm/cap/gnb_mac.pcap and gnb_ngap.pcap
```

Captures are in `/dev/shm/cap` (RAM). Copy them off the node before the experiment ends.

**Known limits**

- ProtO-RU lists the B210, N310 and X410. The X310 and N300 are untested with it.
- The cell is band n78 at the ARFCN srs-rf-matrix uses. That profile runs 20 MHz; 40 MHz
  on the matrix is untested.
- The image isolates cores for a d740 whose experiment NIC is on NUMA node 0.
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
    name="core_on_du",
    description="Run the 5G core on the DU host instead of a separate node",
    typ=portal.ParameterType.BOOLEAN,
    defaultValue=True,
    longDescription="Uses the Open5GS build in the image (bin/fh-core-local.sh), freeing a node for the tap.",
)
pc.defineParameter(
    name="tap_nodetype",
    description="Type of compute node for the tap that bridges and records the fronthaul",
    typ=portal.ParameterType.STRING,
    defaultValue=node_types[0],
    legalValues=node_types,
    longDescription="A d430 takes a firmware interrupt once per second that stalls it for about 430 us, "
                    "so as a tap it holds the fronthaul for that long each second. Use it only to compare.",
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
fh_link = request.Link("fh-du-tap")
fh_link.setNoBandwidthShaping()
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
tap.addService(pg.Execute(shell="bash", command=os.path.join(BIN_PATH, "fh-tap.sh")))

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
