"""
Hierarchical Node Parser for DSPF/SPF netlists.

Handles complex node names commonly seen in commercial parasitic extraction tools
(e.g. StarRC, Quantus):
    CDECR_B0[1]:26
    XXYMUX_BANK0.XXYMUX<1>.MMYMUX_RBL:G
    ld_X3.X18.M15:G

Provides:
- HierNode dataclass
- parse_hier_node() function
- net base name extraction for net2net aggregation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
import re


@dataclass
class HierNode:
    """Structured representation of a hierarchical node name."""
    raw: str
    base_net: str                      # Logical net name (before last ':' or hierarchy)
    hierarchy: List[str] = field(default_factory=list)  # ['XXYMUX_BANK0', 'XXYMUX<1>', ...]
    port: Optional[str] = None         # 'G', '26', '1', etc.
    is_ground: bool = False
    branch: Optional[str] = None       # '1' from net@1 suffix

    def __str__(self) -> str:
        return self.raw

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "base_net": self.base_net,
            "hierarchy": self.hierarchy,
            "port": self.port,
            "is_ground": self.is_ground,
            "branch": self.branch,
        }


def parse_hier_node(node: str) -> HierNode:
    raw_original = node.strip()
    if not raw_original:
        return HierNode(raw=raw_original, base_net=raw_original)

    working = raw_original
    branch: Optional[str] = None
    if "@" in working:
        working, branch = working.rsplit("@", 1)

    lower = working.lower()
    if lower in {"0", "ground", "gnd"}:
        return HierNode(raw=raw_original, base_net="ground", is_ground=True, branch=branch)

    if ":" in working:
        prefix, port = working.rsplit(":", 1)
        base_net = prefix
        hierarchy = [p for p in prefix.split(".") if p]
        return HierNode(
            raw=raw_original,
            base_net=base_net,
            hierarchy=hierarchy,
            port=port,
            branch=branch,
        )

    parts = [p for p in working.split(".") if p]
    if len(parts) > 1:
        return HierNode(
            raw=raw_original,
            base_net=working if branch is not None else parts[-1],
            hierarchy=parts[:-1] if branch is None else parts,
            port=None,
            branch=branch,
        )

    return HierNode(raw=raw_original, base_net=working, branch=branch)


def get_base_net(node: str) -> str:
    """Quick helper to get base net name for aggregation."""
    return parse_hier_node(node).base_net


def is_ground_node(node: str) -> bool:
    return parse_hier_node(node).is_ground
