"""
Export utilities (Stage 4) - Updated for new RCGraphBuilder structure.
"""

from __future__ import annotations

from typing import Dict, Any, Optional

import json
import pandas as pd

from .rc_graph import RCGraphBuilder


def export_net_analysis_to_json(
    rc_graph: RCGraphBuilder,
    net: str,
    include_tc: bool = True,
    include_device_load: bool = True,
) -> Dict[str, Any]:
    """Generate rich analysis dict for CLI / downstream use."""
    ladder = rc_graph.get_rc_ladder_for_net(net, include_tc=include_tc)
    net2net = rc_graph.compute_net2net_totals(net)

    analysis: Dict[str, Any] = {
        "target_net": net,
        "rc_ladder_elements": ladder,
        "net2net_totals": net2net,
        "graph_stats": rc_graph.get_summary_stats(),
    }

    if include_device_load:
        analysis["device_load"] = rc_graph.get_device_load_for_net(net)

    return analysis


def export_to_pandas(rc_graph: RCGraphBuilder, net: Optional[str] = None) -> pd.DataFrame:
    """Export RC elements as pandas DataFrame (Dash/Plotly ready)."""
    records = []
    for u, v, key, data in rc_graph.G.edges(keys=True, data=True):
        record = {
            "from_node": u,
            "to_node": v,
            "edge_key": key,
            "type": data.get("type"),
            "resistance": data.get("resistance"),
            "coupling_cap": data.get("coupling_cap"),
            "tc1": data.get("tc1"),
            "tc2": data.get("tc2"),
            "device": data.get("device"),
            "role": data.get("role"),
        }
        records.append(record)

    df = pd.DataFrame(records)

    if net:
        def matches_net(node: str) -> bool:
            h = rc_graph.node_to_hier.get(node)
            return h.base_net == net if h else False

        mask = df["from_node"].apply(matches_net) | df["to_node"].apply(matches_net)
        df = df[mask]

    return df


def save_analysis_json(analysis: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)
