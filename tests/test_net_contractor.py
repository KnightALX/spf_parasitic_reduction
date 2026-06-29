import pytest
import networkx as nx
from sram_parasitic_toolkit.spf_to_cdl_mapper import CdlScope
from sram_parasitic_toolkit.net_contractor import contract


def build_test_graph_and_mapping():
    G = nx.Graph()
    G.add_node("internal@1", shunt_cap=0.01, base_net="internal")
    G.add_node("internal@2", shunt_cap=0.02, base_net="internal")
    G.add_node("A@1", shunt_cap=0.03, base_net="A")
    G.add_node("B@1", shunt_cap=0.04, base_net="B")

    G.add_edge("internal@1", "internal@2", type="resistor", resistance=50.0)
    G.add_edge("internal@1", "A@1", type="resistor", resistance=100.0)
    G.add_edge("internal@2", "A@1", type="resistor", resistance=200.0)
    G.add_edge("internal@1", "B@1", type="coupling_cap", coupling_cap=0.005)
    G.add_edge("A@1", "B@1", type="kron_effective", resistance=300.0)

    mapping = {
        "internal@1": CdlScope(scope_path="Xtop.mid", local_net="internal", is_port=False, spf_branch="1"),
        "internal@2": CdlScope(scope_path="Xtop.mid", local_net="internal", is_port=False, spf_branch="2"),
        "A@1": CdlScope(scope_path="Xtop.mid", local_net="A", is_port=True, spf_branch="1"),
        "B@1": CdlScope(scope_path="Xtop.mid", local_net="B", is_port=True, spf_branch="1"),
    }
    return G, mapping


def test_contract_merge_groups():
    G, mapping = build_test_graph_and_mapping()
    result = contract(G, mapping)
    assert result.has_node("Xtop.mid::internal")
    assert result.has_node("Xtop.mid::A")
    assert result.has_node("Xtop.mid::B")
    assert result.number_of_nodes() == 3


def test_contract_shunt_cap_sum():
    G, mapping = build_test_graph_and_mapping()
    result = contract(G, mapping)
    internal_shunt = result.nodes["Xtop.mid::internal"]["shunt_cap"]
    assert internal_shunt == 0.03


def test_contract_parallel_inter_group_resistance():
    G, mapping = build_test_graph_and_mapping()
    result = contract(G, mapping)
    assert result.has_edge("Xtop.mid::internal", "Xtop.mid::A")
    edge = result["Xtop.mid::internal"]["Xtop.mid::A"]
    assert "resistance" in edge
    expected_r = 1.0 / (1.0 / 100.0 + 1.0 / 200.0)
    assert abs(edge["resistance"] - expected_r) < 0.01


def test_contract_coupling_cap_preserved():
    G, mapping = build_test_graph_and_mapping()
    result = contract(G, mapping)
    assert result.has_edge("Xtop.mid::internal", "Xtop.mid::B")
    edge = result["Xtop.mid::internal"]["Xtop.mid::B"]
    assert "coupling_cap" in edge
    assert edge["coupling_cap"] == 0.005


def test_contract_intra_group_edge_to_shunt():
    G, mapping = build_test_graph_and_mapping()
    result = contract(G, mapping)
    assert not result.has_edge("Xtop.mid::internal", "Xtop.mid::internal")


def test_contract_orphan_edge_skipped():
    G = nx.Graph()
    G.add_node("internal@1", shunt_cap=0.01)
    G.add_node("ghost@1", shunt_cap=0.02)
    G.add_edge("internal@1", "ghost@1", type="resistor", resistance=100.0)
    mapping = {
        "internal@1": CdlScope(scope_path="Xtop.mid", local_net="internal", is_port=False),
    }
    result = contract(G, mapping)
    assert result.number_of_nodes() == 1
    assert result.number_of_edges() == 0
