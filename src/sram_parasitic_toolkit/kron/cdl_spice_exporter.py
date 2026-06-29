from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Union

import networkx as nx

from ..cdl_parser import HierarchyTree, _instance_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def cdl_back_annotate(
    contracted_graph: nx.Graph,
    hierarchy_tree: HierarchyTree,
    output_path: Union[str, Path],
    prefix: str = "Par_",
    min_r: float = 1e-6,
    min_c: float = 1e-18,
) -> Path:
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    scope_map = _build_scope_map(hierarchy_tree)
    groups: Dict[str, List[str]] = defaultdict(list)

    for node_key in contracted_graph.nodes():
        scope_part = _extract_scope_from_key(node_key)
        if scope_part in scope_map:
            groups[scope_part].append(node_key)
        else:
            groups["__unmatched__"].append(node_key)

    lines: List[str] = []
    now = datetime.now().isoformat(timespec="seconds")
    lines.append("*" + "=" * 78)
    lines.append("* CDL Back-Annotation Parasitic Netlist")
    lines.append(f"* Generated: {now} by spf_parasitic_reduction")
    lines.append("* 将对应段复制到 CDL 相应 .subckt 内部完成反标")
    lines.append("*" + "=" * 78)
    lines.append("")

    r_count = 0
    c_count = 0
    shunt_count = 0

    sorted_scopes = [(k, v) for k, v in sorted(groups.items()) if k != "__unmatched__"]

    for scope_path, node_keys in sorted_scopes:
        subckt_name = scope_map.get(scope_path, scope_path)
        lines.append(f"* >>> 注入到: .subckt {subckt_name} (scope: {scope_path})")
        lines.append("* 以下RC语句使用 {0} 内部的 net 名".format(subckt_name))

        scope_nodes = {k for k in node_keys}

        for u, v, data in contracted_graph.edges(data=True):
            if u not in scope_nodes or v not in scope_nodes:
                continue

            r_val = data.get("resistance")
            if r_val is not None and r_val >= min_r:
                node_u = _local_net_from_key(u)
                node_v = _local_net_from_key(v)
                lines.append(f"{prefix}R_{r_count}  {node_u}  {node_v}  {r_val:.6e}")
                r_count += 1

            c_val = data.get("coupling_cap")
            if c_val is not None and c_val >= min_c:
                node_u = _local_net_from_key(u)
                node_v = _local_net_from_key(v)
                lines.append(f"{prefix}C_{c_count}  {node_u}  {node_v}  {c_val:.6e}")
                c_count += 1

        for node_key in scope_nodes:
            shunt_cap = contracted_graph.nodes[node_key].get("shunt_cap", 0.0)
            if shunt_cap >= min_c:
                net = _local_net_from_key(node_key)
                lines.append(f"{prefix}Cshunt_{shunt_count}  {net}  0  {shunt_cap:.6e}")
                shunt_count += 1

        lines.append(f"* <<< 结束: {subckt_name}")
        lines.append("")

    lines.append("* End of CDL parasitic back-annotation")
    content = "\n".join(lines) + "\n"
    out_path.write_text(content, encoding="utf-8")
    logger.info("CDL back-annotation written: %s (R=%d, C=%d, Shunt=%d)", out_path, r_count, c_count, shunt_count)
    return out_path


def _build_scope_map(tree: HierarchyTree) -> Dict[str, str]:
    scope_map: Dict[str, str] = {}
    _collect_scopes(tree, scope_map)
    return scope_map


def _collect_scopes(tree: HierarchyTree, scope_map: Dict[str, str], prefix: str = "") -> None:
    if not prefix:
        current_path = tree.instance_name
    else:
        current_path = f"{prefix}.{_instance_key(tree.instance_name)}"
    scope_map[current_path] = tree.subckt_def_name
    for inst_key, child in tree.instances.items():
        _collect_scopes(child, scope_map, current_path)


def _extract_scope_from_key(node_key: str) -> str:
    if "::" in node_key:
        return node_key.split("::")[0]
    return node_key


def _local_net_from_key(node_key: str) -> str:
    if "::" in node_key:
        return node_key.split("::")[1]
    return node_key
