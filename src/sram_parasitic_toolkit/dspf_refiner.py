#!/usr/bin/env python3
"""
SRAM Parasitic Analysis Toolkit - Main CLI (dspf_refiner)

Enhanced secondary development on top of eda-netlist-parser.
Implements Stages 1-5:
- RC Graph with networkx (node2node + net2net)
- Hierarchical node parsing
- TC1/TC2 support
- SRAM semantic role classification
- Rich exports (JSON, GraphML, pandas)

Usage:
    python -m sram_parasitic_toolkit.dspf_refiner --spf file.spf --outpath ./out
    python -m sram_parasitic_toolkit.dspf_refiner --spf file.spf --outpath ./out --net NETNAME --graph --tc-aware --sram-role
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from netlist_parser import NetlistParser, NetlistError, Netlist

from .rc_graph import RCGraphBuilder
from .exporters import export_net_analysis_to_json, save_analysis_json, export_to_pandas
from .hier_node import parse_hier_node


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sram-parasitic-toolkit",
        description="DSPF/SPF parser + RC Graph engine for SRAM parasitic analysis",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--spf", required=True, help="Input SPF/DSPF file path")
    parser.add_argument("--outpath", required=True, help="Output directory")
    parser.add_argument("--net", help="Target net (base name or raw node). If omitted, only full summary is generated.")
    parser.add_argument("--graph", action="store_true", help="Export full RC graph to GraphML")
    parser.add_argument("--tc-aware", action="store_true", help="Include TC1/TC2 in resistor edges and output")
    parser.add_argument("--sram-role", action="store_true", help="Add SRAM semantic role classification to device load")
    parser.add_argument("--format", choices=["json", "pandas"], default="json",
                        help="Output format for net-specific analysis")

    args = parser.parse_args()
    execute_refine(args)


def execute_refine(args):
    """Core logic for refine step, extracted for direct calling from run_flow.py."""
    out_dir = Path(args.outpath)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Parsing {args.spf} with eda-netlist-parser...")
    try:
        parser_obj = NetlistParser(internal=False)
        netlist: Netlist = parser_obj.parse(args.spf)
    except (NetlistError, FileNotFoundError) as exc:
        print(f"[ERROR] Failed to parse SPF file '{args.spf}': {exc}")
        sys.exit(1)

    print(f"[INFO] Building RC Graph (networkx MultiGraph)...")
    rc_graph = RCGraphBuilder(netlist)
    stats = rc_graph.get_summary_stats()
    print(f"        Nodes: {stats['num_nodes']}, Edges: {stats['num_edges']}, "
          f"Resistors: {stats['num_resistor_edges']}, Coupling Caps: {stats['num_coupling_edges']}")

    # Always save manifest + graph stats
    manifest = {
        "spf": args.spf,
        "graph_stats": stats,
        "hierarchical_nodes_parsed": len(rc_graph.node_to_hier),
        "unique_base_nets": len(rc_graph.net_to_nodes),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    if args.graph:
        graphml_path = out_dir / "rc_graph.graphml"
        rc_graph.to_graphml(str(graphml_path))
        print(f"[INFO] Full RC graph exported to {graphml_path}")

    if args.net:
        print(f"[INFO] Performing detailed analysis for net: {args.net}")
        # Support both raw node and base_net
        target = args.net
        if target not in rc_graph.net_to_nodes:
            # Try to find matching base_net (exact match on base_net or raw node)
            for base, nodes in rc_graph.net_to_nodes.items():
                if target == base or any(target == n for n in nodes):
                    target = base
                    break

        if target not in rc_graph.net_to_nodes:
            print(f"[ERROR] Target net '{args.net}' not found (no matching base_net or node).")
            sys.exit(1)

        analysis = export_net_analysis_to_json(
            rc_graph,
            target,
            include_tc=args.tc_aware,
            include_device_load=True,
        )

        if args.format == "json":
            out_file = out_dir / f"net_{target.replace(':', '_').replace('.', '_')}.analysis.json"
            save_analysis_json(analysis, str(out_file))
            print(f"[SUCCESS] Analysis saved to {out_file}")
        else:
            df = export_to_pandas(rc_graph, net=target)
            csv_path = out_dir / f"net_{target.replace(':', '_').replace('.', '_')}.csv"
            df.to_csv(csv_path, index=False)
            print(f"[SUCCESS] Pandas DataFrame saved to {csv_path}")

        # Optional: also save a small device load summary when --sram-role
        if args.sram_role:
            print("[INFO] SRAM role classification enabled (see device_load in JSON)")

    else:
        # Full summary mode
        summary_path = out_dir / "dspf_full_summary.json"
        summary = {
            "spf": args.spf,
            "num_cells": len(netlist.cells),
            "graph_stats": stats,
            "base_nets": sorted(rc_graph.net_to_nodes.keys())[:50],  # first 50 for brevity
            "layer_map": netlist.layer_map,
        }
        save_analysis_json(summary, str(summary_path))
        print(f"[SUCCESS] Full summary saved to {summary_path}")

    print("[DONE] SRAM Parasitic Analysis completed.")
