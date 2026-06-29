#!/usr/bin/env python3
"""
Unified SPF Parasitic Flow CLI (run_flow.py)

Supports:
- refine: single step refine (delegates to execute_refine)
- reduce: single step kron reduce
- transfer: single step to SPICE
- pipeline: one-command full flow with --outdir and auto intermediate wiring

Usage examples (run from spf_parasitic_reduction/ directory):
    python run_flow.py refine --spf design.spf --outpath ./results --graph --tc-aware --sram-role
    python run_flow.py reduce --input results/rc_graph.graphml --output results/kron_reduced.graphml --hierarchical --s 0
    python run_flow.py transfer --input results/kron_reduced.graphml --output results/kron_reduced.sp --prefix KronR_ --min-r 1e-3 --min-c 1e-15

    python run_flow.py pipeline --spf design.spf --outdir ./results --graph --tc-aware --sram-role --hierarchical --s 0 --prefix KronR_ --min-r 1e-3 --min-c 1e-15
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import networkx as nx

# Setup paths for direct import (no pip install -e . required)
# Use src layout
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

# Imports using the declared package name
from sram_parasitic_toolkit.dspf_refiner import execute_refine  # type: ignore
from sram_parasitic_toolkit.kron import kron_reducer
from sram_parasitic_toolkit.kron import kron_spice_exporter
from sram_parasitic_toolkit.cdl_parser import parse_cdl
from sram_parasitic_toolkit.spf_to_cdl_mapper import map_nodes
from sram_parasitic_toolkit.net_contractor import contract
from sram_parasitic_toolkit.kron.cdl_spice_exporter import cdl_back_annotate
import json


def add_refine_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--spf", required=True, help="Input SPF/DSPF file path")
    parser.add_argument("--outpath", required=True, help="Output directory")
    parser.add_argument("--net", help="Target net (base name or raw node). If omitted, only full summary is generated.")
    parser.add_argument("--graph", action="store_true", help="Export full RC graph to GraphML")
    parser.add_argument("--tc-aware", action="store_true", help="Include TC1/TC2 in resistor edges and output")
    parser.add_argument("--sram-role", action="store_true", help="Add SRAM semantic role classification to device load")
    parser.add_argument("--format", choices=["json", "pandas"], default="json",
                        help="Output format for net-specific analysis")


def add_reduce_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", "-i", required=True, help="Input .graphml or .gml from refine")
    parser.add_argument("--output", "-o", required=True, help="Output reduced .graphml/.gml")
    parser.add_argument("--hierarchical", action="store_true", help="Enable per-base_net local Kron (recommended for SRAM WL/BL)")
    parser.add_argument("--skip-global-kron", action="store_true",
                        help="Skip the final global Kron step after merging local reductions")
    parser.add_argument("--s", type=complex, default=0j, help="s value for Y(s) e.g. 1j*2*pi*1e9 (default DC=0)")
    parser.add_argument("--boundary-strategy", default="non_ground_device",
                        choices=["non_ground_device", "non_ground", "all"],
                        help="How to auto-select ports/boundary nodes")
    parser.add_argument("--no-map-cap", action="store_true", help="Do not map internal shunt_cap to boundary")


def add_transfer_args(parser: argparse.ArgumentParser) -> None:
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


def add_back_annotate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kron-graph", required=True, help="Kron-reduced .graphml file")
    parser.add_argument("--cdl", required=True, help="CDL netlist file")
    parser.add_argument("--output", "-o", required=True, help="Output .sp file path")
    parser.add_argument("--prefix", default="Par_", help="Instance name prefix")
    parser.add_argument("--min-r", type=float, default=1e-3, help="Minimum resistance to keep (Ohm)")
    parser.add_argument("--min-c", type=float, default=1e-15, help="Minimum capacitance to keep (F)")


def cmd_refine(args: argparse.Namespace) -> None:
    print("[INFO] Running refine step...")
    execute_refine(args)
    print("[SUCCESS] Refine step completed.")


def cmd_reduce(args: argparse.Namespace) -> None:
    print("[INFO] Running reduce step...")
    G = kron_reducer.KronReducer.load_graph(args.input)
    reducer = kron_reducer.KronReducer(G)

    if args.hierarchical:
        do_global = not args.skip_global_kron
        reduced = reducer.hierarchical_kron_reduce(
            s=args.s,
            local_boundary_strategy=args.boundary_strategy,
            do_global=do_global
        )
    else:
        reduced = reducer.reduce_kron(s=args.s, boundary_strategy=args.boundary_strategy)

    kron_reducer.KronReducer.save_graph(reduced, args.output)
    print(f"[SUCCESS] Reduce step completed. Output: {args.output}")


def cmd_transfer(args: argparse.Namespace) -> None:
    print("[INFO] Running transfer step...")
    p = Path(args.input)
    if p.suffix.lower() in (".graphml", ".xml"):
        G = nx.read_graphml(p)
    else:
        try:
            G = nx.read_graphml(p)
        except Exception:
            G = nx.read_gml(p)

    kron_spice_exporter.reduced_graph_to_spice(
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
    print(f"[SUCCESS] Transfer step completed. Output: {args.output}")


def cmd_back_annotate(args: argparse.Namespace) -> None:
    print("[INFO] Running back-annotate step...")

    print("[INFO] Loading Kron-reduced graph from:", args.kron_graph)
    G = kron_reducer.KronReducer.load_graph(args.kron_graph)

    print("[INFO] Parsing CDL file:", args.cdl)
    hierarchy_tree = parse_cdl(args.cdl)

    kron_nodes = list(G.nodes())
    print(f"[INFO] Mapping {len(kron_nodes)} Kron nodes to CDL scopes...")
    mapping, unmapped = map_nodes(kron_nodes, hierarchy_tree)

    print(f"[INFO] Mapped: {len(mapping)}, Unmapped: {len(unmapped)}")

    print("[INFO] Contracting nodes...")
    contracted = contract(G, mapping, hierarchy_tree)
    print(f"[INFO] Contracted graph: {contracted.number_of_nodes()} nodes, {contracted.number_of_edges()} edges")

    print("[INFO] Generating CDL back-annotation SPICE file...")
    cdl_back_annotate(
        contracted,
        hierarchy_tree,
        args.output,
        prefix=args.prefix,
        min_r=args.min_r,
        min_c=args.min_c,
    )

    report_path = Path(args.output).parent / "back_annotate_report.json"
    report = {
        "spf_file": getattr(args, "spf", "unknown"),
        "cdl_file": args.cdl,
        "total_spf_nodes_in_kron_graph": len(kron_nodes),
        "mapped_nodes": len(mapping),
        "unmapped_nodes": len(unmapped),
        "unmapped_details": unmapped,
    }
    report_path.write_text(json.dumps(report, indent=2, encoding="utf-8"), encoding="utf-8")
    print(f"[INFO] Report saved to: {report_path}")

    print(f"[SUCCESS] Back-annotate step completed. Output: {args.output}")


def cmd_pipeline(args: argparse.Namespace) -> None:
    print("[INFO] Running full pipeline...")
    out_dir = Path(args.outdir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Refine
    print("[pipeline] Stage 1: refine")
    refine_args = argparse.Namespace(
        spf=args.spf,
        outpath=str(out_dir),
        net=None,  # full summary for pipeline
        graph=getattr(args, 'graph', False),
        tc_aware=getattr(args, 'tc_aware', False),
        sram_role=getattr(args, 'sram_role', False),
        format="json",
    )
    execute_refine(refine_args)

    graph_path = out_dir / "rc_graph.graphml"
    if not graph_path.exists() and getattr(args, 'graph', False):
        print("[WARNING] Expected rc_graph.graphml not found. Check --graph flag.")

    # Step 2: Reduce
    print("[pipeline] Stage 2: reduce")
    reduced_path = out_dir / "kron_reduced.graphml"
    reduce_args = argparse.Namespace(
        input=str(graph_path) if graph_path.exists() else str(out_dir / "rc_graph.graphml"),
        output=str(reduced_path),
        hierarchical=getattr(args, 'hierarchical', False),
        skip_global_kron=getattr(args, 'skip_global_kron', False),
        s=getattr(args, 's', 0j),
        boundary_strategy=getattr(args, 'boundary_strategy', 'non_ground_device'),
        no_map_cap=getattr(args, 'no_map_cap', False),
    )
    # If user provided explicit input for reduce, override
    if hasattr(args, 'reduce_input') and args.reduce_input:
        reduce_args.input = args.reduce_input
    cmd_reduce(reduce_args)

    # Step 3: Transfer
    print("[pipeline] Stage 3: transfer")
    final_sp = out_dir / "kron_reduced.sp"
    transfer_args = argparse.Namespace(
        input=str(reduced_path),
        output=str(final_sp),
        subckt_name=getattr(args, 'subckt_name', None),
        prefix=getattr(args, 'prefix', 'Kron'),
        min_r=getattr(args, 'min_r', 1e-6),
        min_c=getattr(args, 'min_c', 1e-18),
        no_shunt=getattr(args, 'no_shunt', False),
        resistor_model=getattr(args, 'resistor_model', None),
        no_model_line=getattr(args, 'no_model_line', False),
    )
    # Allow override
    if hasattr(args, 'transfer_input') and args.transfer_input:
        transfer_args.input = args.transfer_input
    cmd_transfer(transfer_args)

    if getattr(args, 'back_annotate', False):
        if not getattr(args, 'cdl', None):
            print("[ERROR] --cdl is required when --back-annotate is set")
            sys.exit(1)
        print("[pipeline] Stage 4: back-annotate")
        ba_output = out_dir / "cdl_parasitic.sp"
        ba_args = argparse.Namespace(
            kron_graph=str(reduced_path),
            cdl=args.cdl,
            output=str(ba_output),
            prefix=getattr(args, 'prefix', 'Par_'),
            min_r=getattr(args, 'min_r', 1e-3),
            min_c=getattr(args, 'min_c', 1e-15),
        )
        cmd_back_annotate(ba_args)

    print("[SUCCESS] Pipeline completed. Outputs in:", out_dir)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="run_flow",
        description="Unified CLI for SPF parasitic analysis (refine + reduce + transfer)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # refine
    p_refine = subparsers.add_parser("refine", help="Run refine step only")
    add_refine_args(p_refine)
    p_refine.set_defaults(func=cmd_refine)

    # reduce
    p_reduce = subparsers.add_parser("reduce", help="Run reduce step only")
    add_reduce_args(p_reduce)
    p_reduce.set_defaults(func=cmd_reduce)

    # transfer
    p_transfer = subparsers.add_parser("transfer", help="Run transfer step only")
    add_transfer_args(p_transfer)
    p_transfer.set_defaults(func=cmd_transfer)

    # back-annotate
    p_back_annotate = subparsers.add_parser("back-annotate", help="Run CDL back-annotation step")
    add_back_annotate_args(p_back_annotate)
    p_back_annotate.set_defaults(func=cmd_back_annotate)

    # pipeline
    p_pipeline = subparsers.add_parser("pipeline", help="Run full refine -> reduce -> transfer in one go")
    p_pipeline.add_argument("--outdir", required=True, help="Output directory for the full pipeline")
    # Refine args without --outpath (use --outdir for auto)
    p_pipeline.add_argument("--spf", required=True, help="Input SPF/DSPF file path")
    p_pipeline.add_argument("--net", help="Target net (base name or raw node). If omitted, only full summary is generated.")
    p_pipeline.add_argument("--graph", action="store_true", help="Export full RC graph to GraphML")
    p_pipeline.add_argument("--tc-aware", action="store_true", help="Include TC1/TC2 in resistor edges and output")
    p_pipeline.add_argument("--sram-role", action="store_true", help="Add SRAM semantic role classification to device load")
    p_pipeline.add_argument("--format", choices=["json", "pandas"], default="json",
                            help="Output format for net-specific analysis")
    # reduce/transfer control (no --input to avoid conflict with auto)
    p_pipeline.add_argument("--hierarchical", action="store_true", help="Enable per-base_net local Kron (recommended for SRAM WL/BL)")
    p_pipeline.add_argument("--skip-global-kron", action="store_true",
                            help="Skip the final global Kron step after merging local reductions")
    p_pipeline.add_argument("--s", type=complex, default=0j, help="s value for Y(s) e.g. 1j*2*pi*1e9 (default DC=0)")
    p_pipeline.add_argument("--boundary-strategy", default="non_ground_device",
                            choices=["non_ground_device", "non_ground", "all"],
                            help="How to auto-select ports/boundary nodes")
    p_pipeline.add_argument("--no-map-cap", action="store_true", help="Do not map internal shunt_cap to boundary")

    p_pipeline.add_argument("--subckt-name", default=None,
                            help="If set, wrap content in .subckt ... .ends (default: flat include style)")
    p_pipeline.add_argument("--prefix", default="Kron", help="Instance name prefix (default: Kron)")
    p_pipeline.add_argument("--min-r", type=float, default=1e-6, help="Minimum resistance to keep (Ohm)")
    p_pipeline.add_argument("--min-c", type=float, default=1e-18, help="Minimum capacitance to keep (F)")
    p_pipeline.add_argument("--no-shunt", action="store_true", help="Do not emit grounded shunt capacitors")
    p_pipeline.add_argument("--resistor-model", default=None,
                            help="Custom resistor model name, e.g. resStar (matches your original SPF style)")
    p_pipeline.add_argument("--no-model-line", action="store_true",
                            help="Do not emit .model resStar line (if already defined in your PDK include)")
    p_pipeline.add_argument("--back-annotate", action="store_true", help="Run CDL back-annotation after transfer")
    p_pipeline.add_argument("--cdl", default=None, help="CDL netlist file (required when --back-annotate)")
    p_pipeline.set_defaults(func=cmd_pipeline)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
