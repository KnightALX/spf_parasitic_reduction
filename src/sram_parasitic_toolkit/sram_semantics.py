"""
SRAM Semantic Role Classification.

Provides lightweight inference of device roles based on device_name,
parameters, and node context. Designed to be extended with your
specific bitcell / SA / WL naming conventions.
"""

from __future__ import annotations

from typing import Optional

from .hier_node import HierNode


def infer_sram_role(device_name: Optional[str], parameters: dict, nodes: list[str]) -> str:
    """
    Infer high-level SRAM role for a DeviceInstance.

    Current heuristics (extend as needed):
    - FinFET / advanced node indicators (nfin, asej, sa/sb)
    - Common device_name patterns (pulvt, nch, pch, resstar, etc.)
    - Node context (G for gate, D/S for drain/source)
    """
    if not device_name:
        return "unknown"

    dn = device_name.lower()
    params = {k.lower(): v for k, v in parameters.items()}

    # FinFET / GAA indicators
    if any(k in params for k in ["nfin", "nfinp", "nfinm", "asej", "adej"]):
        if "p" in dn or "pu" in dn or "pullup" in dn:
            return "pullup_fin"
        if "n" in dn or "pd" in dn or "pulldown" in dn:
            return "pulldown_fin"
        if "pg" in dn or "pass" in dn or "access" in dn:
            return "passgate_fin"
        return "mos_fin"

    # Classic planar / older nodes
    if "resstar" in dn or dn.startswith("r"):
        return "parasitic_resistor"

    if any(x in dn for x in ["pulvt", "nch", "nmos", "pd"]):
        return "pulldown"
    if any(x in dn for x in ["pch", "pmos", "pu"]):
        return "pullup"
    if any(x in dn for x in ["pg", "pass", "access"]):
        return "passgate"

    # X instances (subckt) — often bitcell or SA wrappers
    if dn.startswith("x"):
        if any(kw in dn for kw in ["bitcell", "sram", "sa", "sense"]):
            return "sram_subckt"
        return "hierarchical_instance"

    # Default
    if "r" in dn[:1]:
        return "resistor"
    if "c" in dn[:1]:
        return "capacitor"

    return "other"


def is_mos_device(role: str) -> bool:
    return role in {"pullup", "pulldown", "passgate", "pullup_fin", "pulldown_fin", "passgate_fin", "mos_fin"}


def is_parasitic_rc(role: str) -> bool:
    return role in {"parasitic_resistor", "resistor", "capacitor"}
