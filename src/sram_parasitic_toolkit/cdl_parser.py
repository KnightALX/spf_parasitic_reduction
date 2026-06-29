from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class HierarchyTree:
    subckt_def_name: str
    instance_name: str = ""
    ports: List[str] = field(default_factory=list)
    local_nets: Set[str] = field(default_factory=set)
    instances: Dict[str, "HierarchyTree"] = field(default_factory=dict)
    instance_port_map: Dict[str, Dict[str, str]] = field(default_factory=dict)
    parent: Optional["HierarchyTree"] = None

    @property
    def scope_name(self) -> str:
        if self.parent is None:
            return self.subckt_def_name
        return f"{self.parent.scope_name}.{self.instance_name}"


def parse_cdl(filepath_or_text: str, is_text: bool = False) -> HierarchyTree:
    if is_text:
        lines = filepath_or_text.splitlines()
    else:
        path = Path(filepath_or_text)
        lines = path.read_text(encoding="utf-8").splitlines()
    return _parse_lines(lines)


def _preprocess_lines(raw_lines: List[str]) -> List[str]:
    processed: List[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("*") or stripped.startswith("$"):
            continue
        if stripped.startswith("+"):
            if processed:
                processed[-1] = processed[-1] + " " + stripped[1:].strip()
            continue
        processed.append(stripped)
    return processed


def _tokenize(line: str) -> List[str]:
    return line.split()


def _is_instance(line: str) -> bool:
    return line.startswith("X") or line.startswith("x")


def _is_subckt(line: str) -> bool:
    return line.lower().startswith(".subckt")


def _is_ends(line: str) -> bool:
    return line.lower().startswith(".ends")


def _parse_lines(lines: List[str]) -> HierarchyTree:
    processed = _preprocess_lines(lines)

    subckt_registry: Dict[str, Dict] = {}
    current_subckt: Optional[Dict] = None

    for line in processed:
        if _is_subckt(line):
            tokens = _tokenize(line)
            name = tokens[1]
            ports = tokens[2:]
            current_subckt = {
                "name": name,
                "ports": ports,
                "local_nets": set(ports),
                "instances": [],
            }
            subckt_registry[name] = current_subckt
            continue

        if _is_ends(line) and current_subckt is not None:
            current_subckt = None
            continue

        if current_subckt is not None:
            if _is_instance(line):
                tokens = _tokenize(line)
                inst_name = tokens[0]
                subckt_type = tokens[-1]
                port_connections = tokens[1:-1]
                current_subckt["instances"].append((inst_name, port_connections, subckt_type))
                current_subckt["local_nets"].update(port_connections)
            else:
                tokens = _tokenize(line)
                if tokens and not tokens[0].startswith("."):
                    current_subckt["local_nets"].update(tokens)

    top_instance_line = None
    for line in reversed(processed):
        if _is_instance(line) and not line.lower().startswith(".subckt"):
            top_instance_line = line
            break

    if top_instance_line is None:
        raise ValueError("No top-level instance (X line) found in CDL")

    return _build_hierarchy_tree(top_instance_line, subckt_registry)


def _instance_key(inst_name: str) -> str:
    return inst_name[1:] if (inst_name.startswith("X") or inst_name.startswith("x")) else inst_name


def _build_hierarchy_tree(
    top_line: str,
    registry: Dict[str, Dict],
    parent: Optional[HierarchyTree] = None,
) -> HierarchyTree:
    tokens = _tokenize(top_line)
    inst_name = tokens[0]
    port_connections = tokens[1:-1]
    subckt_type = tokens[-1]

    if subckt_type not in registry:
        raise ValueError(f"Subcircuit definition '{subckt_type}' not found in CDL (referenced by {inst_name})")

    subckt_def = registry[subckt_type]

    tree = HierarchyTree(
        subckt_def_name=subckt_type,
        instance_name=inst_name,
        ports=list(subckt_def["ports"]),
        local_nets=set(subckt_def["local_nets"]),
        parent=parent,
    )

    port_map: Dict[str, Dict[str, str]] = {}
    for child_inst_name, child_port_cons, child_type in subckt_def["instances"]:
        child_line = f"{child_inst_name} " + " ".join(child_port_cons) + f" {child_type}"
        child_tree = _build_hierarchy_tree(child_line, registry, parent=tree)
        tree.instances[_instance_key(child_inst_name)] = child_tree

        if child_type in registry:
            child_def = registry[child_type]
            child_ports = child_def["ports"]
            port_map[_instance_key(child_inst_name)] = {}
            for j, port in enumerate(child_ports):
                if j < len(child_port_cons):
                    port_map[_instance_key(child_inst_name)][port] = child_port_cons[j]

    tree.instance_port_map = port_map
    return tree
