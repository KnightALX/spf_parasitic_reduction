import pytest
from sram_parasitic_toolkit.cdl_parser import parse_cdl
from sram_parasitic_toolkit.spf_to_cdl_mapper import map_nodes, CdlScope

CDL_FIXTURE = """\
.subckt leaf_subckt  A  B  VDD  VSS
M0 A B net1 VSS nch w=1u l=0.1u
M1 net1 A VDD VDD pch w=2u l=0.1u
.ends leaf_subckt

.subckt mid_subckt  in  out  VDD  VSS
Xleaf1  in  internal  VDD  VSS  leaf_subckt
Xleaf2  internal  out  VDD  VSS  leaf_subckt
.ends mid_subckt

.subckt top_subckt  data  result  VDD  VSS
Xmid  data  result  VDD  VSS  mid_subckt
.ends top_subckt

Xtop  PAD_DATA  PAD_RESULT  VDD  VSS  top_subckt
"""


def test_map_simple_node():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    kron_nodes = ["Xtop.mid.internal@1"]
    mapping, unmapped = map_nodes(kron_nodes, tree)
    assert "Xtop.mid.internal@1" in mapping
    scope = mapping["Xtop.mid.internal@1"]
    assert scope.scope_path == "Xtop.mid"
    assert scope.local_net == "internal"
    assert scope.is_port is False
    assert scope.spf_branch == "1"


def test_map_node_with_branch():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    kron_nodes = ["Xtop.mid.leaf1.A@2"]
    mapping, unmapped = map_nodes(kron_nodes, tree)
    scope = mapping["Xtop.mid.leaf1.A@2"]
    assert scope.scope_path == "Xtop.mid.leaf1"
    assert scope.local_net == "A"
    assert scope.is_port is True


def test_map_unmapped_node():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    kron_nodes = ["Xtop.ghost.ghost_net@1"]
    mapping, unmapped = map_nodes(kron_nodes, tree)
    assert len(mapping) == 0
    assert len(unmapped) == 1
    assert unmapped[0]["spf_node"] == "Xtop.ghost.ghost_net@1"
    assert "reason" in unmapped[0]


def test_map_multiple_nodes_same_net():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    kron_nodes = [
        "Xtop.mid.internal@1",
        "Xtop.mid.internal@2",
        "Xtop.mid.internal@3",
    ]
    mapping, unmapped = map_nodes(kron_nodes, tree)
    assert len(mapping) == 3
    for node in kron_nodes:
        scope = mapping[node]
        assert scope.local_net == "internal"
        assert scope.scope_path == "Xtop.mid"


def test_map_port_node():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    kron_nodes = ["Xtop.mid.out@1"]
    mapping, unmapped = map_nodes(kron_nodes, tree)
    scope = mapping["Xtop.mid.out@1"]
    assert scope.scope_path == "Xtop.mid"
    assert scope.local_net == "out"
    assert scope.is_port is True
