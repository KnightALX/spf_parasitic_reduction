# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SRAM Parasitic Analysis Toolkit (v0.3.0) — Python toolkit for post-processing DSPF/SPF netlists in advanced SRAM design flows (TSMC N5/N7, FinFET/GAA). Built as secondary development on top of `eda-netlist-parser`.

## Commands

### Install
```bash
pip install -r requirements.txt
```

### CLI usage (main entry point)
```bash
# Full parse + summary
python -m sram_parasitic_toolkit.dspf_refiner --spf design.spf --outpath ./results

# Detailed per-net analysis with RC graph + TC-aware resistors + SRAM role classification
python -m sram_parasitic_toolkit.dspf_refiner \
    --spf design.spf --outpath ./results \
    --net NETNAME --graph --tc-aware --sram-role
```

### Programmatic usage
See `examples/run_sample.py` for the minimal pattern: parse SPF with `NetlistParser`, build `RCGraphBuilder`, then query via `get_summary_stats()` / `compute_net2net_totals()` / `to_graphml()`.

There is no test suite, linter config, or build configuration in this repo. Module imports serve as the smoke check.

## Architecture

Pipeline: **SPF file → NetlistParser → RCGraphBuilder → query methods → exporters**.

### `hier_node.py` — Node name parsing
- `HierNode` dataclass: `raw`, `base_net`, `hierarchy` (list of path segments), `port`, `is_ground`.
- `parse_hier_node()` handles commercial-tool patterns: `CDECR_B0[1]:26`, `XXYMUX_BANK0.XXYMUX<1>.MMYMUX_RBL:G`, `ld_X3.X18.M15:G`.
- Strategy: detect ground (`0`/`ground`/`gnd`) → split on last `:` for port → split remaining on `.` for hierarchy path.
- `base_net` is the net used for net2net aggregation; raw node names are the graph vertex IDs.

### `rc_graph.py` — Core RC graph engine
- Wraps a `networkx.MultiGraph` (`self.G`) keyed on raw node names.
- **Critical design constraint: GraphML compatibility.** Only scalar/simple-dict attributes are stored on nodes/edges. Rich semantic data (device load, full parameter dicts) is kept in a separate `self._device_load` dict and returned via query methods — never attached to the graph itself, because `networkx.write_graphml` chokes on nested structures.
- Capacitor handling: a cap whose `n2` is ground is folded into `shunt_cap` on `n1`; otherwise it becomes a `coupling_cap` edge for net2net coupling analysis.
- Resistor edges carry `resistance`, `tc1`, `tc2` (TC1/TC2 are required for corner-aware timing).
- Public API: `get_node2node_subgraph`, `get_rc_ladder_for_net`, `compute_net2net_totals`, `get_device_load_for_net`, `to_graphml`, `get_summary_stats`.

### `sram_semantics.py` — Device role inference
- `infer_sram_role()` returns a string label: `pullup`, `pulldown`, `passgate`, `pullup_fin`, `pulldown_fin`, `passgate_fin`, `mos_fin`, `parasitic_resistor`, `resistor`, `capacitor`, `sram_subckt`, `hierarchical_instance`, `other`.
- Heuristics look at: FinFET/GAA indicators in parameters (`nfin`, `asej`, `adej`), device_name substrings (`pulvt`, `nch`, `pch`, `resstar`, `pg`/`pass`/`access`), and `x`-prefixed subcircuit instances containing `bitcell`/`sram`/`sa`/`sense`.
- This is intentionally lightweight — extend with your bitcell/SA/WL naming conventions rather than rewriting.

### `exporters.py` — Output formats
- `export_net_analysis_to_json()` — rich per-net analysis dict (ladder elements + net2net totals + device load + graph stats).
- `export_to_pandas()` — flat DataFrame of all edges; filterable by net via base_net match. Designed as the data layer for a future Dash dashboard.
- `save_analysis_json()` — pretty-printed JSON with `ensure_ascii=False` (preserves any non-ASCII net names from commercial tools).

### `dspf_refiner.py` — CLI orchestrator
- Argparse: `--spf`, `--outpath`, `--net`, `--graph`, `--tc-aware`, `--sram-role`, `--format {json,pandas}`.
- Always writes `manifest.json` with graph stats; writes per-net analysis only when `--net` is given; writes full summary (`dspf_full_summary.json`) when no `--net`.
- The `--net` arg accepts either a raw node name or a base net; if not found directly, it falls back to substring match across `net_to_nodes`.

## External dependency
This package depends on `eda-netlist-parser` (imported as `from netlist_parser import NetlistParser, Netlist, NetlistError, DeviceInstance`). It is not vendored — must be installed via `pip install eda-netlist-parser>=0.1.2`.

## Extension points (from README)
- Swap in a Rust `dspf-parse` backend for very large SPF files.
- Add NSGA-II / Bayesian optimization interface over the RC graph.
- Build Dash dashboard on top of the pandas export.

## Note for Grok users
This document is retained for Claude compatibility. See .grok/skills/ for equivalent personal skills (sram-eda-expert etc.) and docs/superpowers/ for plans. Recent changes focused on minimal hygiene fixes only.