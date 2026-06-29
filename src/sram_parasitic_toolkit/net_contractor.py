from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import networkx as nx

from .spf_to_cdl_mapper import CdlScope
from .cdl_parser import HierarchyTree

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def contract(
    kron_graph: nx.Graph,
    mapping: Dict[str, CdlScope],
    hierarchy_tree: Optional[HierarchyTree] = None,
) -> nx.Graph:
    merge_groups: Dict[str, List[str]] = {}
    node_to_group: Dict[str, str] = {}

    for spf_node, scope in mapping.items():
        if spf_node not in kron_graph:
            continue
        group_key = f"{scope.scope_path}::{scope.local_net}"
        if group_key not in merge_groups:
            merge_groups[group_key] = []
        merge_groups[group_key].append(spf_node)
        node_to_group[spf_node] = group_key

    result = nx.Graph()

    for group_key, spf_nodes in merge_groups.items():
        total_shunt = 0.0
        for n in spf_nodes:
            total_shunt += kron_graph.nodes[n].get("shunt_cap", 0.0)
        result.add_node(group_key, shunt_cap=total_shunt, num_merged=len(spf_nodes))

    for u, v, data in kron_graph.edges(data=True):
        g_u = node_to_group.get(u)
        g_v = node_to_group.get(v)

        if g_u is None or g_v is None:
            continue

        if g_u == g_v:
            c_val = data.get("coupling_cap") or data.get("coupling_cap_approx", 0.0)
            result.nodes[g_u]["shunt_cap"] += c_val
        else:
            _merge_parallel_edge(result, g_u, g_v, data)

    _finalize_edges(result)
    return result


def _merge_parallel_edge(result: nx.Graph, g_u: str, g_v: str, edge_data: Dict[str, Any]) -> None:
    if not result.has_edge(g_u, g_v):
        result.add_edge(g_u, g_v, resistance_sum_inv=0.0, coupling_cap_sum=0.0,
                         effective_conductance_sum=0.0, has_any_edge=False)

    existing = result[g_u][g_v]
    existing["has_any_edge"] = True

    r_val = edge_data.get("resistance")
    if r_val is not None and r_val > 1e-12 and r_val < 1e12:
        existing["resistance_sum_inv"] += 1.0 / r_val

    g_val = edge_data.get("effective_conductance", 0.0)
    if g_val > 0:
        existing["effective_conductance_sum"] += g_val

    c_val = edge_data.get("coupling_cap") or edge_data.get("coupling_cap_approx", 0.0)
    if c_val > 0:
        existing["coupling_cap_sum"] += c_val


def _finalize_edges(result: nx.Graph) -> None:
    for u, v, data in list(result.edges(data=True)):
        if not data.get("has_any_edge"):
            result.remove_edge(u, v)
            continue

        if data.get("resistance_sum_inv", 0.0) > 0:
            data["resistance"] = 1.0 / data["resistance_sum_inv"]
            data["type"] = "resistor"
            del data["resistance_sum_inv"]
        elif data.get("effective_conductance_sum", 0.0) > 0:
            data["resistance"] = 1.0 / data["effective_conductance_sum"]
            data["type"] = "kron_effective"
            del data["effective_conductance_sum"]

        if data.get("coupling_cap_sum", 0.0) > 0:
            data["coupling_cap"] = data["coupling_cap_sum"]
            del data["coupling_cap_sum"]

        if "has_any_edge" in data:
            del data["has_any_edge"]
