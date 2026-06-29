import pytest
import networkx as nx
from pathlib import Path
from sram_parasitic_toolkit.cdl_parser import parse_cdl
from sram_parasitic_toolkit.kron.cdl_spice_exporter import cdl_back_annotate

CDL_FIXTURE = """\
.subckt leaf_subckt  A  B  VDD  VSS
M0 A B net1 VSS nch w=1u l=0.1u
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


def build_contracted_graph():
    G = nx.Graph()
    G.add_node("Xtop.mid::internal", shunt_cap=0.05)
    G.add_node("Xtop.mid::in", shunt_cap=0.01)
    G.add_node("Xtop.mid::out", shunt_cap=0.02)
    G.add_edge("Xtop.mid::internal", "Xtop.mid::in", type="resistor", resistance=100.0)
    G.add_edge("Xtop.mid::internal", "Xtop.mid::out", type="coupling_cap", coupling_cap=0.003)
    return G


def test_cdl_back_annotate_creates_output(tmp_path: Path):
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    G = build_contracted_graph()
    out_path = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(G, tree, str(out_path), prefix="Par_")
    assert out_path.exists()


def test_cdl_back_annotate_contains_section_markers(tmp_path: Path):
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    G = build_contracted_graph()
    out_path = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(G, tree, str(out_path), prefix="Par_")
    content = out_path.read_text(encoding="utf-8")
    assert ">>> 注入到: .subckt mid_subckt" in content
    assert "<<< 结束: mid_subckt" in content


def test_cdl_back_annotate_uses_local_net_names(tmp_path: Path):
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    G = build_contracted_graph()
    out_path = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(G, tree, str(out_path), prefix="Par_")
    content = out_path.read_text(encoding="utf-8")
    assert "internal" in content
    assert "in" in content
    assert "out" in content
    assert "Xtop.mid::internal" not in content


def test_cdl_back_annotate_emits_resistors(tmp_path: Path):
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    G = build_contracted_graph()
    out_path = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(G, tree, str(out_path), prefix="Par_")
    content = out_path.read_text(encoding="utf-8")
    assert "Par_R_" in content


def test_cdl_back_annotate_ground_mapped_to_zero(tmp_path: Path):
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    G = build_contracted_graph()
    out_path = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(G, tree, str(out_path), prefix="Par_")
    content = out_path.read_text(encoding="utf-8")
    assert "Cshunt_Par" in content or "Par_Cshunt" in content
