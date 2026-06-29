import pytest
from sram_parasitic_toolkit.cdl_parser import parse_cdl, HierarchyTree

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


def test_parse_cdl_root_instance():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    assert tree is not None
    assert tree.instance_name == "Xtop"
    assert tree.subckt_def_name == "top_subckt"
    assert tree.ports == ["data", "result", "VDD", "VSS"]


def test_parse_cdl_hierarchy():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    assert "mid" in tree.instances
    mid = tree.instances["mid"]
    assert mid.instance_name == "Xmid"
    assert mid.subckt_def_name == "mid_subckt"
    assert mid.ports == ["in", "out", "VDD", "VSS"]


def test_parse_cdl_local_nets():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    mid = tree.instances["mid"]
    assert "internal" in mid.local_nets
    assert "in" in mid.local_nets
    assert "out" in mid.local_nets


def test_parse_cdl_deep_leaf():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    leaf1 = tree.instances["mid"].instances["leaf1"]
    assert leaf1.instance_name == "Xleaf1"
    assert leaf1.subckt_def_name == "leaf_subckt"
    assert leaf1.ports == ["A", "B", "VDD", "VSS"]
    assert "net1" in leaf1.local_nets
    assert "A" in leaf1.local_nets


def test_parse_cdl_instance_port_map():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    mid = tree.instances["mid"]
    assert mid.instance_port_map is not None
    assert mid.instance_port_map["leaf1"]["A"] == "in"
    assert mid.instance_port_map["leaf1"]["B"] == "internal"
    assert mid.instance_port_map["leaf2"]["A"] == "internal"
    assert mid.instance_port_map["leaf2"]["B"] == "out"


def test_parse_cdl_parent_reference():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    mid = tree.instances["mid"]
    assert mid.parent is tree
    leaf1 = mid.instances["leaf1"]
    assert leaf1.parent is mid


def test_parse_cdl_scope_name():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    leaf1 = tree.instances["mid"].instances["leaf1"]
    assert leaf1.scope_name == "top_subckt.Xmid.Xleaf1"
