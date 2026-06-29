#!/usr/bin/env python3
"""
kron_spice_exporter.py
Convert Kron-reduced .graphml / .gml parasitic graph into a clean SPICE netlist (.sp)
containing ONLY R and C instantiations.

Purpose:
- Take the output of kron_reducer.py (reduced graph with resistance + coupling_cap)
- Generate a .sp file that can be directly .include'd into pre-layout (pre-sim) netlists
  for fast functional / timing verification in SRAM or analog flows.

Features:
- Flat R/C instantiations using original node names (easy to include)
- Optional .subckt wrapper mode
- Ground handling (is_ground nodes → 0)
- Shunt caps → grounded C to 0
- Coupling caps → floating C between nodes
- Resistors → R between nodes
- Smart naming with prefix to avoid collision
- Min R / min C filtering
- Rich header comments (source file, Kron s-value, stats, date)

Fits perfectly after:
    dspf_refiner.py → kron_reducer.py (hierarchical + s-domain) → kron_spice_exporter.py

Usage (standalone):
    python kron_spice_exporter.py \
        --input  design_kron_reduced.graphml \
        --output design_kron_reduced.sp \
        --subckt-name kron_parasitic \
        --prefix KronR_

Or import in your pipeline:
    from kron_spice_exporter import reduced_graph_to_spice
    reduced_graph_to_spice(G, Path("reduced.sp"), subckt_name="kron_wl_bl")

Author: Grok (rc-reduction-expert + sram-eda-expert integration)
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import networkx as nx

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def reduced_graph_to_spice(
    G: Union[nx.Graph, nx.MultiGraph],
    output_path: Union[str, Path],
    subckt_name: Optional[str] = None,
    prefix: str = "Kron",
    min_r: float = 1e-6,
    min_c: float = 1e-18,
    include_grounded_caps: bool = True,
    resistor_model: Optional[str] = None,      # e.g. "resStar" to match your PDK model
    emit_model_definition: bool = True,
) -> Path:
    """
    Convert a Kron-reduced parasitic graph into SPICE netlist (R + C only).

    Supports custom resistor model (e.g. resStar) to match your original SPF style:
        .model resStar R Tref=25
        Rxxx node1 node2 resStar R=123.45

    Args:
        G: Kron-reduced graph (needs 'resistance' or 'effective_conductance' on edges)
        output_path: Output .sp path
        subckt_name: Optional .subckt wrapper name
        prefix: Instance name prefix
        min_r / min_c: Filtering thresholds
        include_grounded_caps: Emit node shunt_cap as grounded C
        resistor_model: If set (e.g. "resStar"), emit resistors using this model
        emit_model_definition: Whether to emit ".model resStar ..." line at top

    Returns:
        Path to generated .sp file
    """
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = []
    now = datetime.now().isoformat(timespec="seconds")

    # ====================== HEADER ======================
    lines.append("*" + "=" * 78)
    lines.append("* Kron-reduced RC parasitic netlist (R + C only)")
    lines.append(f"* Generated: {now}")
    lines.append(f"* Source graph: Kron reduction output")
    lines.append(f"* Nodes: {G.number_of_nodes()}, Edges: {G.number_of_edges()}")
    lines.append("* Purpose: Fast pre-sim include (functional / timing check)")
    lines.append("* Note: Off-diagonal effective_y_real < 0 is normal (passive admittance)")
    lines.append("*       Use 'resistance' and 'coupling_cap' attributes for simulation")
    lines.append("*" + "=" * 78)
    lines.append("")

    # Collect ground nodes
    gnd_nodes = {n for n, d in G.nodes(data=True) if d.get("is_ground", False)}

    # ====================== SUBCKT (optional) ======================
    if subckt_name:
        ports = [n for n in G.nodes() if n not in gnd_nodes]
        port_list = " ".join(ports)
        lines.append(f".subckt {subckt_name} {port_list}")
        lines.append("")

    # ====================== RESISTORS (support custom model like resStar) ======================
    r_count = 0
    if resistor_model and emit_model_definition:
        lines.append(f".model {resistor_model} R Tref=25")
        lines.append("")

    for u, v, data in G.edges(data=True):
        r_val = data.get("resistance")
        if r_val is None:
            g = data.get("effective_conductance")
            if g and g > 0:
                r_val = 1.0 / g
        if r_val is None or r_val < min_r:
            continue

        r_name = f"{prefix}R_{r_count}"
        node_u = u if u not in gnd_nodes else "0"
        node_v = v if v not in gnd_nodes else "0"

        if resistor_model:
            # resStar style: Rxxx n1 n2 resStar R=123.45
            lines.append(f"{r_name} {node_u} {node_v} {resistor_model} R={r_val:.6e}")
        else:
            # Classic plain resistor
            lines.append(f"{r_name} {node_u} {node_v} {r_val:.6e}")
        r_count += 1

    lines.append(f"* Total resistors: {r_count}")
    lines.append("")

    # ====================== COUPLING CAPACITORS (floating) ======================
    c_count = 0
    for u, v, data in G.edges(data=True):
        c_val = data.get("coupling_cap") or data.get("coupling_cap_approx")
        if c_val is None or c_val < min_c:
            continue

        c_name = f"{prefix}C_{c_count}"
        node_u = u if u not in gnd_nodes else "0"
        node_v = v if v not in gnd_nodes else "0"

        lines.append(f"{c_name} {node_u} {node_v} {c_val:.6e}")
        c_count += 1

    lines.append(f"* Total coupling capacitors: {c_count}")
    lines.append("")

    # ====================== SHUNT / GROUNDED CAPACITORS ======================
    if include_grounded_caps:
        shunt_count = 0
        for node, d in G.nodes(data=True):
            c_val = d.get("shunt_cap", 0.0)
            if c_val < min_c:
                continue
            c_name = f"{prefix}Cshunt_{shunt_count}"
            node_name = node if node not in gnd_nodes else "0"
            lines.append(f"{c_name} {node_name} 0 {c_val:.6e}")
            shunt_count += 1
        lines.append(f"* Total grounded (shunt) capacitors: {shunt_count}")
        lines.append("")

    # ====================== FOOTER ======================
    if subckt_name:
        lines.append(f".ends {subckt_name}")
        lines.append("")

    lines.append("* End of Kron-reduced parasitic netlist")
    lines.append("* You can .include this file in your pre-sim testbench.")
    lines.append("* All node names match the original DSPF hierarchical names.")

    # Write file
    content = "\n".join(lines) + "\n"
    out_path.write_text(content, encoding="utf-8")

    logger.info("SPICE netlist written: %s", out_path)
    logger.info("  Resistors: %d, Coupling Caps: %d, Shunt Caps: %d",
                r_count, c_count, shunt_count if include_grounded_caps else 0)

    return out_path


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main_cli():
    parser = argparse.ArgumentParser(
        description="Convert Kron-reduced .graphml/.gml parasitic graph to SPICE .sp netlist (R+C only)"
    )
    parser.add_argument("--input", "-i", required=True, help="Kron-reduced .graphml or .gml file")
    parser.add_argument("--output", "-o", required=True, help="Output .sp netlist path")
    parser.add_argument("--subckt-name", default=None,
                        help="If set, wrap content in .subckt ... .ends (default: flat include style)")
    parser.add_argument("--prefix", default="Kron", help="Instance name prefix (default: Kron)")
    parser.add_argument("--min-r", type=float, default=1e-6, help="Minimum resistance to keep (Ohm)")
    parser.add_argument("--min-c", type=float, default=1e-18, help="Minimum capacitance to keep (F)")
    parser.add_argument("--no-shunt", action="store_true", help="Do not emit grounded shunt capacitors")
    parser.add_argument("--resistor-model", default=None,
                        help="Custom resistor model name, e.g. resStar (matches your original SPF style)")
    parser.add_argument("--no-model-line", action="store_true",
                        help="Do not emit .model resStar line (if already defined in your PDK include)")

    args = parser.parse_args()

    # Load graph (support both graphml and gml)
    p = Path(args.input)
    if p.suffix.lower() in (".graphml", ".xml"):
        G = nx.read_graphml(p)
    else:
        try:
            G = nx.read_graphml(p)
        except Exception:
            G = nx.read_gml(p)

    reduced_graph_to_spice(
        G,
        args.output,
        subckt_name=args.subckt_name,
        prefix=args.prefix,
        min_r=args.min_r,
        min_c=args.min_c,
        include_grounded_caps=not args.no_shunt,
        resistor_model=args.resistor_model,
        emit_model_definition=not args.no_model_line,
    )


if __name__ == "__main__":
    main_cli()
