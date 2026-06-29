from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .cdl_parser import HierarchyTree

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class CdlScope:
    scope_path: str
    local_net: str
    is_port: bool = False
    spf_branch: Optional[str] = None


def map_nodes(
    kron_nodes: List[str],
    hierarchy_tree: HierarchyTree,
) -> Tuple[Dict[str, CdlScope], List[Dict]]:
    mapping: Dict[str, CdlScope] = {}
    unmapped: List[Dict] = []

    for node in kron_nodes:
        scope = _map_single_node(node, hierarchy_tree)
        if scope is None:
            unmapped.append({"spf_node": node, "reason": "Node not found in CDL hierarchy"})
        else:
            mapping[node] = scope

    logger.info("Mapped %d nodes, %d unmapped", len(mapping), len(unmapped))
    return mapping, unmapped


def _map_single_node(
    spf_node: str,
    root: HierarchyTree,
) -> Optional[CdlScope]:
    branch: Optional[str] = None
    working = spf_node.strip()
    if "@" in working:
        working, branch = working.rsplit("@", 1)

    if working.lower() in {"0", "ground", "gnd"}:
        return CdlScope(scope_path="", local_net="0", is_port=False, spf_branch=branch)

    if ":" in working:
        working, _ = working.rsplit(":", 1)

    parts = [p for p in working.split(".") if p]
    if not parts:
        return None

    return _resolve_path(parts, root, spf_node, branch)


def _resolve_path(
    path_parts: List[str],
    current: HierarchyTree,
    original_node: str,
    branch: Optional[str],
) -> Optional[CdlScope]:
    if len(path_parts) == 0:
        return None

    first = path_parts[0]

    if first == current.instance_name:
        if len(path_parts) == 1:
            return None
        net_candidate = path_parts[-1]
        instance_parts = path_parts[1:-1]

        target = current
        instance_path_segments = [current.instance_name]
        for inst_part in instance_parts:
            if inst_part in target.instances:
                target = target.instances[inst_part]
                instance_path_segments.append(inst_part)
            else:
                logger.warning(
                    "Instance '%s' not found in '%s' for node '%s'",
                    inst_part, target.subckt_def_name, original_node,
                )
                return None

        scope_path = ".".join(instance_path_segments)
        search_scope = target

        while search_scope is not None:
            if net_candidate in search_scope.local_nets:
                is_port = net_candidate in search_scope.ports
                return CdlScope(
                    scope_path=scope_path,
                    local_net=net_candidate,
                    is_port=is_port,
                    spf_branch=branch,
                )
            search_scope = search_scope.parent

        logger.warning(
            "Net '%s' not found in any scope for node '%s'",
            net_candidate, original_node,
        )
        return None

    if first in current.instances:
        return _resolve_path(path_parts, current.instances[first], original_node, branch)

    logger.warning(
        "Instance '%s' not found in '%s' for node '%s'",
        first, current.subckt_def_name, original_node,
    )
    return None
