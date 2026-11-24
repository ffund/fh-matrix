#!/usr/bin/env python
"""O-RAN 7.2 fronthaul capture with a commercial O-RU on the attenuator matrix.

Branch `benetel` of the fh-matrix profile repository. The OCUDU O-DU (fronthaul-d740-u26rt-uhd410
image) drives a Benetel RAN650 through POWDER's COTS O-RU mechanism instead of ProtO-RU + USRP.
"""
import os

import geni.portal as portal
import geni.rspec.pg as pg
import geni.rspec.igext as ig
import geni.rspec.emulab.pnext as pn
import geni.rspec.emulab as emulab
import geni.rspec.emulab.cotsru
import geni.rspec.emulab.lanext

tourDescription = """
### O-RAN 7.2 fronthaul capture: OCUDU O-DU + Benetel RAN650 O-RU on the attenuator matrix

**Exploratory. First bring-up of a commercial O-RU with our O-DU image.**

- `cn5g`: Open5GS core (d430), installed as PowderTeam/srs-rf-matrix does it.
- `du`: OCUDU O-DU and CU (d740, image `fronthaul-d740-u26rt-uhd410`). Its fronthaul port links
  to the O-RU through the experiment switch, which also delivers PTP (G.8275.1) to both ends.
- `ru`: Benetel RAN650 (`bru-650-5` or `bru-650-6`), configured by POWDER from the parameters here.
  Users cannot log in to it.
- `ue1`: `nuc16`, the COTS UE wired to those O-RUs through the matrix.

The settings for the RAN650 come from POWDER's `dmaas/bru-matrix-test` profile.
"""

tourInstructions = """
See `/opt/fh/README.md` on `du` and the notes in the fh-matrix repository. On `du`:

```
geni-get manifest > /tmp/mani.xml      # RU MAC address, VLAN, PTP interface
tail /opt/fh/run/fh-setup.log
```
"""

BIN_PATH = "/local/repository/bin"
UBUNTU_IMG = "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU22-64-STD"
FH_IMG = "urn:publicid:IDN+emulab.net+image+nyunetworks//fronthaul-d740-u26rt-uhd410"
COTS_UE_IMG = "urn:publicid:IDN+emulab.net+image+PowderTeam:cots-jammy-image"
COMP_MANAGER_ID = "urn:publicid:IDN+emulab.net+authority+cm"
OPEN5GS_DEPLOY_SCRIPT = os.path.join(BIN_PATH, "deploy-open5gs.sh")

pc = portal.Context()
node_types = [("d430", "Emulab, d430"), ("d740", "Emulab, d740"), ("d760p", "Emulab, d760p")]
pc.defineParameter("ru_id", "Benetel RAN650 on the matrix", portal.ParameterType.STRING, "bru-650-5",
                   legalValues=[("bru-650-5", "bru-650-5"), ("bru-650-6", "bru-650-6")])
pc.defineParameter("ue_id", "COTS UE NUC wired to that O-RU", portal.ParameterType.STRING, "nuc16")
pc.defineParameter("bandwidth", "Cell bandwidth in MHz", portal.ParameterType.INTEGER, 20,
                   legalValues=[(20, "20 MHz"), (40, "40 MHz"), (100, "100 MHz")])
pc.defineParameter("dl_arfcn", "Downlink NR-ARFCN (band n78)", portal.ParameterType.INTEGER, 624668,
                   longDescription="624668 is 3370.02 MHz, the value in POWDER's RAN650 config.")
pc.defineParameter("compression", "Fronthaul compression header mode", portal.ParameterType.STRING, "static",
                   legalValues=[("static", "static"), ("dynamic", "dynamic")])
pc.defineParameter("vlan_id", "VLAN tag for the fronthaul link (0 = let the testbed choose)",
                   portal.ParameterType.INTEGER, 0, advanced=True)
pc.defineParameter("mimo_mode", "RU MIMO mode string (empty = RU default)", portal.ParameterType.STRING, "", advanced=True)
pc.defineParameter("du_nodetype", "Type of compute node for the O-DU", portal.ParameterType.STRING,
                   node_types[1], legalValues=node_types, advanced=True)
pc.defineParameter("cn_nodetype", "Type of compute node for the core", portal.ParameterType.STRING,
                   node_types[0], legalValues=node_types, advanced=True)
pc.defineParameter("fh_image", "Disk image for the O-DU", portal.ParameterType.STRING, FH_IMG, advanced=True)
params = pc.bindParameters()
pc.verifyParameters()
request = pc.makeRequestRSpec()

# Core network
cn_node = request.RawPC("cn5g")
cn_node.component_manager_id = COMP_MANAGER_ID
cn_node.hardware_type = params.cn_nodetype
cn_node.disk_image = UBUNTU_IMG
cn_if = cn_node.addInterface("cn5g-if")
cn_if.addAddress(pg.IPv4Address("192.168.1.1", "255.255.255.0"))
cn_link = request.Link("cn5g-link")
cn_link.setNoBandwidthShaping()
cn_link.addInterface(cn_if)
cn_node.addService(pg.Execute(shell="bash", command=OPEN5GS_DEPLOY_SCRIPT))

# O-DU. The testbed switch is the PTP grandmaster; ask for PTP on the fronthaul port.
du = request.RawPC("du")
du.component_manager_id = COMP_MANAGER_ID
du.hardware_type = params.du_nodetype
du.disk_image = params.fh_image
du_cn_if = du.addInterface("du-cn-if")
du_cn_if.addAddress(pg.IPv4Address("192.168.1.2", "255.255.255.0"))
cn_link.addInterface(du_cn_if)
du_ofh_if = du.addInterface("du-ofh-if")
du_ofh_if.PTP()

# O-RU, configured by the testbed from these settings.
ru = request.COTSRU("ru", component_id=params.ru_id, arfcn=params.dl_arfcn, bandwidth=params.bandwidth,
                    ofh_compression_mode=params.compression, mimo_mode=(params.mimo_mode or None))
ru.component_manager_id = COMP_MANAGER_ID
ru_ofh_if = ru.addInterface("ru-ofh-if")
ru_ofh_if.PTP()
ru_ofh_if.SyncE()
ru.Desire("rf-controlled", 1)

ofh_link = request.Link("ofh-link", members=[du_ofh_if, ru_ofh_if])
ofh_link.setNoBandwidthShaping()
ofh_link.vlan_tagging = True
if params.vlan_id:
    ofh_link.setVlanTag(params.vlan_id)

# COTS UE
ue = request.RawPC("ue1")
ue.component_manager_id = COMP_MANAGER_ID
ue.component_id = params.ue_id
ue.disk_image = COTS_UE_IMG
ue.Desire("rf-controlled", 1)
ue.addService(pg.Execute(shell="bash", command="/local/repository/bin/module-airplane.sh"))
ue.addService(pg.Execute(shell="bash", command="/local/repository/bin/setup-cots-ue.sh internet"))

# RF link through the attenuator matrix
ru_rf_if = ru.addInterface("ru_ue1_rf")
ue_rf_if = ue.addInterface("ue1_ru_rf")
rf_link = request.RFLink("rflink_ru_ue1")
rf_link.addInterface(ru_rf_if)
rf_link.addInterface(ue_rf_if)

tour = ig.Tour()
tour.Description(ig.Tour.MARKDOWN, tourDescription)
tour.Instructions(ig.Tour.MARKDOWN, tourInstructions)
request.addTour(tour)
pc.printRequestRSpec(request)
