import json
import networkx as nx
from pathlib import Path
from sram_parasitic_toolkit.cdl_parser import parse_cdl
from sram_parasitic_toolkit.spf_to_cdl_mapper import map_nodes
from sram_parasitic_toolkit.net_contractor import contract
from sram_parasitic_toolkit.kron.cdl_spice_exporter import cdl_back_annotate

CDL = """\
.subckt buf_subckt  A  Y  VDD  VSS
M0 A net1 VSS VSS nch w=2u l=0.1u
M1 Y net1 VDD VDD pch w=4u l=0.1u
M2 net1 A VDD VDD pch w=1u l=0.1u
.ends buf_subckt

.subckt top_subckt  in  out  VDD  VSS
Xbuf1  in  mid  VDD  VSS  buf_subckt
Xbuf2  mid  out  VDD  VSS  buf_subckt
.ends top_subckt

Xtop  PAD_IN  PAD_OUT  VDD  VSS  top_subckt
"""


def build_kron_graph():
    G = nx.Graph()
    G.add_node("Xtop.buf1.net1@1", shunt_cap=0.01)
    G.add_node("Xtop.buf1.net1@2", shunt_cap=0.02)
    G.add_node("Xtop.buf1.A@1", shunt_cap=0.005)
    G.add_node("Xtop.buf2.A@1", shunt_cap=0.006)
    G.add_node("Xtop.mid@1", shunt_cap=0.03)
    G.add_node("Xtop.mid@2", shunt_cap=0.04)

    G.add_edge("Xtop.buf1.net1@1", "Xtop.buf1.net1@2", type="resistor", resistance=50.0)
    G.add_edge("Xtop.buf1.net1@1", "Xtop.buf1.A@1", type="resistor", resistance=100.0)
    G.add_edge("Xtop.buf1.net1@2", "Xtop.buf1.A@1", type="resistor", resistance=150.0)
    G.add_edge("Xtop.buf1.A@1", "Xtop.mid@1", type="resistor", resistance=75.0)
    G.add_edge("Xtop.buf2.A@1", "Xtop.mid@1", type="resistor", resistance=80.0)
    G.add_edge("Xtop.mid@1", "Xtop.mid@2", type="coupling_cap", coupling_cap=0.002)
    G.add_edge("Xtop.mid@2", "Xtop.buf2.A@1", type="resistor", resistance=90.0)

    return G


def test_integration_e2e(tmp_path: Path):
    tree = parse_cdl(CDL, is_text=True)
    kron_graph = build_kron_graph()
    kron_nodes = list(kron_graph.nodes())

    mapping, unmapped = map_nodes(kron_nodes, tree)
    assert len(mapping) > 0
    assert all(u["spf_node"] not in mapping for u in unmapped) or len(unmapped) == 0

    contracted = contract(kron_graph, mapping, tree)
    assert contracted.number_of_nodes() > 0
    assert contracted.number_of_edges() > 0

    out_sp = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(contracted, tree, str(out_sp), prefix="Par_")

    content = out_sp.read_text(encoding="utf-8")
    assert ">>> 注入到: .subckt buf_subckt" in content
    assert ">>> 注入到: .subckt top_subckt" in content

    assert "Xtop.buf1.net1@1" not in content
    assert "Xtop.buf1.net1@2" not in content

    assert "net1" in content
    assert "mid" in content
    assert "A" in content


def test_integration_no_floating_nodes(tmp_path: Path):
    tree = parse_cdl(CDL, is_text=True)
    kron_graph = build_kron_graph()
    kron_nodes = list(kron_graph.nodes())
    mapping, unmapped = map_nodes(kron_nodes, tree)
    contracted = contract(kron_graph, mapping, tree)
    out_sp = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(contracted, tree, str(out_sp), prefix="Par_")
    content = out_sp.read_text(encoding="utf-8")

    reported_nets = set()
    for line in content.splitlines():
        if line.startswith("Par_R_") or line.startswith("Par_C_"):
            parts = line.split()
            if len(parts) >= 4:
                reported_nets.add(parts[1])
                reported_nets.add(parts[2])
        elif line.startswith("Par_Cshunt_"):
            parts = line.split()
            if len(parts) >= 3:
                reported_nets.add(parts[1])

    reported_nets.discard("0")

    all_scopes = [tree]
    all_scopes.append(tree.instances.get("buf1"))
    all_scopes.append(tree.instances.get("buf2"))
    all_scopes = [s for s in all_scopes if s is not None]

    for net in reported_nets:
        found = False
        for scope in all_scopes:
            if net in scope.local_nets:
                found = True
                break
        assert found, f"Net '{net}' used in output but not found in any CDL scope"
