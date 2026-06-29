# SRAM Parasitic Flow Tool

A single command-line tool that turns a DSPF/SPF parasitic file into a reduced SPICE netlist with CDL back-annotation support in four steps:

1. **Refine** — Parse the SPF and build an RC graph.
2. **Reduce** — Apply Kron reduction to shrink the network.
3. **Transfer** — Export the reduced network as a clean `.sp` file you can `.include` in simulation.
4. **Back-annotate** — Map SPF nodes to CDL hierarchy, merge `@` branches, and generate injection-ready per-subckt SPICE segments.

Run the steps one at a time or let `pipeline` do everything automatically.

## Setup

No installation or `pip install -e .` is required.

```bash
cd spf_parasitic_reduction
python run_flow.py --help
```

You need the dependencies listed in `requirements.txt`:

```bash
pip install -r requirements.txt
```

## Quick Start (Recommended)

### Basic pipeline (SPF → Kron-reduced SPICE)

```bash
python run_flow.py pipeline \
    --spf design.spf \
    --outdir ./results \
    --graph \
    --tc-aware \
    --sram-role \
    --hierarchical \
    --s 0 \
    --prefix KronR_ \
    --min-r 1e-3 \
    --min-c 1e-15
```

This produces:

```
results/
├── rc_graph.graphml          # full RC graph from refine
├── kron_reduced.graphml      # Kron-reduced graph
├── kron_reduced.sp           # final SPICE netlist (ready to include)
└── manifest.json             # summary of the refine step
```

### Full pipeline with CDL back-annotation

```bash
python run_flow.py pipeline \
    --spf design.spf \
    --cdl design.cdl \
    --outdir ./results \
    --graph --tc-aware --sram-role \
    --hierarchical --s 0 \
    --back-annotate \
    --prefix Par_ --min-r 1e-3 --min-c 1e-15
```

This produces:

```
results/
├── manifest.json               # refine statistics
├── rc_graph.graphml            # full RC graph
├── kron_reduced.graphml        # Kron-reduced graph
├── kron_reduced.sp             # flat SPICE netlist
├── cdl_parasitic.sp            # CDL back-annotation netlist
└── back_annotate_report.json   # mapping quality report
```

## The Five Commands

### 1. refine — Build RC graph from SPF

```bash
python run_flow.py refine \
    --spf design.spf \
    --outpath ./results \
    --graph \
    --tc-aware \
    --sram-role
```

**Key parameters**

| Flag            | Description                                      |
|-----------------|--------------------------------------------------|
| `--spf`         | Input DSPF/SPF file (required)                   |
| `--outpath`     | Output directory (required)                      |
| `--graph`       | Write `rc_graph.graphml`                         |
| `--tc-aware`    | Include TC1/TC2 on resistors                     |
| `--sram-role`   | Add SRAM device role classification              |
| `--net NAME`    | Analyze only this net (writes per-net JSON/CSV)  |
| `--format`      | `json` (default) or `pandas` when using `--net`  |

**Outputs**
- `manifest.json` (always)
- `rc_graph.graphml` (with `--graph`)
- `dspf_full_summary.json` (when no `--net`)
- `net_xxx.analysis.json` or `.csv` (when `--net` is used)

### 2. reduce — Kron reduction

```bash
python run_flow.py reduce \
    --input results/rc_graph.graphml \
    --output results/kron_reduced.graphml \
    --hierarchical \
    --s 0
```

**Key parameters**

| Flag                  | Description                                      |
|-----------------------|--------------------------------------------------|
| `--input`, `-i`       | Input .graphml (required)                        |
| `--output`, `-o`      | Output .graphml (required)                       |
| `--hierarchical`      | Reduce per base_net first (recommended)          |
| `--s VALUE`           | Complex frequency for Y(s) (e.g. `1j*2*pi*1e9`) |
| `--boundary-strategy` | `non_ground_device` (default), `non_ground`, `all` |

### 3. transfer — Export to SPICE

```bash
python run_flow.py transfer \
    --input results/kron_reduced.graphml \
    --output results/kron_reduced.sp \
    --prefix KronR_ \
    --min-r 1e-3 \
    --min-c 1e-15
```

**Key parameters**

| Flag                | Description                                      |
|---------------------|--------------------------------------------------|
| `--input`, `-i`     | Reduced .graphml (required)                      |
| `--output`, `-o`    | Output .sp file (required)                       |
| `--prefix`          | Instance name prefix (default: `Kron`)           |
| `--min-r`           | Skip resistors below this value (Ohm)            |
| `--min-c`           | Skip capacitors below this value (F)             |
| `--subckt-name`     | Wrap result in `.subckt ... .ends`               |
| `--resistor-model`  | Emit resistors using a PDK model (e.g. `resStar`) |

### 4. back-annotate — CDL hierarchy-aware back-annotation

```bash
python run_flow.py back-annotate \
    --kron-graph results/kron_reduced.graphml \
    --cdl design.cdl \
    --output results/cdl_parasitic.sp \
    --prefix Par_ \
    --min-r 1e-3 \
    --min-c 1e-15
```

**Key parameters**

| Flag              | Description                                      |
|-------------------|--------------------------------------------------|
| `--kron-graph`    | Kron-reduced .graphml (required)                 |
| `--cdl`           | CDL netlist file (required)                      |
| `--output`, `-o`  | Output .sp file (required)                       |
| `--prefix`        | Instance name prefix (default: `Par_`)           |
| `--min-r`         | Minimum resistance to keep (Ohm)                 |
| `--min-c`         | Minimum capacitance to keep (F)                  |

**How it works**

The back-annotate step solves the floating-node problem when including `.sp` files into CDL testbenches:

1. Parses the CDL netlist to build a subcircuit hierarchy tree
2. Maps Kron-reduced SPF node names (e.g. `Xtop.xmmio.net147@1`) to CDL-scoped local net names (e.g. `net147` inside `mmio_subckt`)
3. Merges `@` branch suffixes — multiple physical branches of the same logical net are contracted into one node with summed parasitics
4. Outputs per-subckt injection-ready SPICE segments that use CDL-local net names

**Outputs**
- `cdl_parasitic.sp` — injection-ready per-subckt parasitic netlist
- `back_annotate_report.json` — mapping statistics and unmatched node details

**Sample content of `cdl_parasitic.sp`** (excerpt):

```spice
* CDL Back-Annotation Parasitic Netlist
* Generated: 2026-06-29 by spf_parasitic_reduction
* 将对应段复制到 CDL 相应 .subckt 内部完成反标
*
* >>> 注入到: .subckt mmio_subckt (scope: Xtop.xmmio)
* 以下RC语句使用 mmio_subckt 内部的 net 名
Par_R_0  net147  net148  2.727e+01
Par_C_0  net147  net149  5.000e-17
Par_Cshunt_0  net147  0  1.200e-16
* <<< 结束: mmio_subckt

* >>> 注入到: .subckt top_subckt (scope: Xtop)
Par_R_10  data_in  addr  5.000e+02
* <<< 结束: top_subckt
```

Copy the sections between `>>>` and `<<<` markers into the corresponding `.subckt` block in your CDL netlist to complete back-annotation.

### 5. pipeline — One-command flow (recommended)

```bash
python run_flow.py pipeline \
    --spf design.spf \
    --outdir ./results \
    --graph --tc-aware --sram-role \
    --hierarchical --s 0 \
    --prefix KronR_ --min-r 1e-3 --min-c 1e-15
```

All flags from the four steps are accepted. The tool automatically wires:

- refine output → reduce input
- reduce output → transfer input
- transfer output → back-annotate input (when `--back-annotate --cdl design.cdl`)

Use `--outdir` to control the root folder. All intermediate files land there with the names shown above.

## Tips

- Run with `--help` on any subcommand for the complete flag list.
- In `pipeline` mode you can still override any stage by passing its flags.
- For very large designs use `--hierarchical` in reduce/pipeline.
- The tool never modifies your original SPF or CDL file.
- Check `back_annotate_report.json` after back-annotation to verify mapping quality. Unmatched nodes are logged with reasons.

## Architecture (for reference)

- `sram_parasitic_toolkit/` — core logic (under `src/`)
  - `dspf_refiner.py` — SPF parsing + RC graph
  - `hier_node.py` — hierarchical node name parsing (supports `@` branch suffixes)
  - `cdl_parser.py` — CDL netlist hierarchy tree builder
  - `spf_to_cdl_mapper.py` — SPF node → CDL scope mapping
  - `net_contractor.py` — node contraction with parallel RC merging
  - `kron/` — Kron reduction and SPICE export
    - `kron_reducer.py` — Kron reduction engine (Schur complement)
    - `kron_spice_exporter.py` — flat SPICE netlist export
    - `cdl_spice_exporter.py` — per-subckt injection-style CDL SPICE export
- `run_flow.py` — user-facing CLI
- `tests/` — unit and integration tests (25 passing)

See the files under `src/sram_parasitic_toolkit/` for implementation details.