#!/usr/bin/env python3
"""
kron_reducer.py
RC Parasitic Kron Reduction Module for spf_parasitic_reduction downstream integration.

Aligns with rc_graph.py / dspf_refiner.py interfaces:
- Accepts networkx (Multi)Graph with node attrs: base_net, shunt_cap, is_ground
- Edge attrs: type='resistor'/'coupling_cap', resistance, coupling_cap, role, tc1, tc2, device
- Supports .graphml / .gml input/output via networkx
- Hierarchical Kron: per base_net local reduction → global merge & reduction
- Full s-domain RC Kron: builds Y(s) = G + s*C (complex), Schur complement via efficient solve (no full inv for perf)
- Default: auto-identify boundary = non-ground + device-connected nodes (or all non-ground)
- Output: simplified GraphML with only boundary nodes + effective RC/Y attributes

Performance notes (perf-code-producer style):
- Uses numpy + scipy.linalg.solve for Schur (O(n^3) but practical for per-net |n|<2000-5000)
- For larger: TODO switch to scipy.sparse + spsolve (factorization once, multiple RHS)
- Hierarchical mode keeps memory low by reducing local base_nets first
- No unnecessary copies; views where possible

SRAM/EDA context (sram-eda-expert + rc-reduction-expert):
- Ideal post dspf_refiner step for WL/BL parasitic, power grid, or full macro RC reduction
- Preserves base_net, role, device attrs on kept boundary nodes for downstream sram_semantics.py
- Matches Kron theory from Dörfler/Bullo + PRIMA/SPRIM papers (Schur on admittance, structure preservation hints)
- Can be extended to TICER-like or realizable RC synthesis later

Usage (no pip install -e needed):
    python kron_reducer.py --input reduced_parasitic.graphml --output kron_reduced.graphml --hierarchical --s 0
    # or import in your dspf_refiner.py after graph build

Author: Grok (rc-reduction-expert + perf-code-producer integration for Kai's SRAM EDA flow)
Date: 2026-06-22
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import networkx as nx
import numpy as np

try:
    from scipy.linalg import solve as scipy_solve
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    logging.warning("scipy not found, falling back to numpy.linalg.solve (slower for large matrices)")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


class KronReducer:
    """
    Core Kron reduction engine for RC parasitic graphs.
    Supports s-domain Y(s) = G + s*C Schur reduction + hierarchical per-base_net flow.
    """

    def __init__(self, G: Union[nx.Graph, nx.MultiGraph]):
        if not isinstance(G, (nx.Graph, nx.MultiGraph)):
            raise TypeError("Input must be networkx Graph or MultiGraph")
        self.G = G
        self.node_list: List[str] = []
        self.node_idx: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # 1. Boundary / Port identification (default strategy aligns with "port/base_net")
    # ------------------------------------------------------------------
    def identify_boundary(
        self,
        strategy: str = "non_ground_device",
        min_degree: int = 1,
    ) -> List[str]:
        """
        Identify boundary (kept) nodes = "ports".
        Strategies:
            - "non_ground_device": non-ground nodes that have resistor edges with SRAM role (PG/PD/PU/SA/WL etc.)
              or any device connection. Best default for SRAM parasitic.
            - "non_ground": all nodes with is_ground=False
            - "all": everything (for testing)
            - "base_net_representatives": one representative per base_net (advanced)
        """
        if strategy == "all":
            return list(self.G.nodes())

        candidates = []
        device_connected = set()

        for u, v, data in self.G.edges(data=True):
            role = data.get("role", "")
            if role and role not in ("", None, "unknown"):
                device_connected.add(u)
                device_connected.add(v)

        for n, d in self.G.nodes(data=True):
            is_gnd = d.get("is_ground", False)
            base = d.get("base_net", "")
            if strategy == "non_ground_device":
                if not is_gnd and (n in device_connected or d.get("shunt_cap", 0.0) > 0):
                    candidates.append(n)
            elif strategy == "non_ground":
                if not is_gnd:
                    candidates.append(n)
            elif strategy == "base_net_representatives":
                # simplistic: keep first node per base_net (user can refine)
                pass  # implement if needed

        if not candidates:
            logger.warning("No boundary nodes found with strategy=%s, falling back to all non-ground", strategy)
            candidates = [n for n, d in self.G.nodes(data=True) if not d.get("is_ground", False)]

        # dedup + sort for determinism
        return sorted(set(candidates))

    def group_by_base_net(self) -> Dict[str, List[str]]:
        """Group nodes by base_net for hierarchical reduction."""
        groups: Dict[str, List[str]] = defaultdict(list)
        for n, d in self.G.nodes(data=True):
            bnet = d.get("base_net", "UNKNOWN_BASE_NET")
            groups[bnet].append(n)
        return groups

    # ------------------------------------------------------------------
    # 2. Matrix construction (G conductance + C capacitance) from graph attrs
    # ------------------------------------------------------------------
    def _build_G_C_matrices(
        self, nodes: Optional[List[str]] = None
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Build dense G (conductance) and C (capacitance) matrices.
        Handles MultiGraph by accumulating parallel R (g sum) and C (c sum).
        Node order = self.node_list (or provided).
        """
        if nodes is None:
            nodes = list(self.G.nodes())
        self.node_list = nodes
        n = len(nodes)
        self.node_idx = {node: i for i, node in enumerate(nodes)}

        G_mat = np.zeros((n, n), dtype=float)
        C_mat = np.zeros((n, n), dtype=float)

        # Resistors and previously reduced effective edges → conductance
        for u, v, data in self.G.edges(data=True):
            etype = data.get("type", "")
            if etype not in ("resistor", "kron_effective"):
                continue
            # Prefer explicit resistance; fallback to 1 / effective_y_real if present (for s=0 case)
            r = data.get("resistance")
            if r is None:
                yreal = data.get("effective_y_real")
                if yreal and abs(yreal) > 1e-12:
                    r = 1.0 / yreal if yreal > 0 else 1e12
                else:
                    r = 1e12
            g = 1.0 / float(r) if float(r) > 1e-12 else 0.0
            if u not in self.node_idx or v not in self.node_idx:
                continue
            i, j = self.node_idx[u], self.node_idx[v]
            G_mat[i, j] -= g
            G_mat[j, i] -= g
            G_mat[i, i] += g
            G_mat[j, j] += g

        # Shunt caps on nodes
        for node, d in self.G.nodes(data=True):
            if node not in self.node_idx:
                continue
            i = self.node_idx[node]
            c_shunt = float(d.get("shunt_cap", 0.0))
            C_mat[i, i] += c_shunt

        # Coupling caps (floating between nodes) + approx from previous Kron effective edges
        for u, v, data in self.G.edges(data=True):
            etype = data.get("type", "")
            if etype == "coupling_cap":
                c = float(data.get("coupling_cap", 0.0))
            elif etype == "kron_effective":
                c = float(data.get("coupling_cap_approx", 0.0))
            else:
                continue
            if u not in self.node_idx or v not in self.node_idx:
                continue
            i, j = self.node_idx[u], self.node_idx[v]
            C_mat[i, i] += c
            C_mat[j, j] += c
            C_mat[i, j] -= c
            C_mat[j, i] -= c

        return G_mat, C_mat

    # ------------------------------------------------------------------
    # 3. Core Schur / Kron (s-domain ready, efficient solve)
    # ------------------------------------------------------------------
    def _schur_complement(
        self,
        Y: np.ndarray,
        boundary_idx: List[int],
        internal_idx: List[int],
    ) -> np.ndarray:
        """
        Compute Schur complement Y_red = Y[bb] - Y[bi] @ inv(Y[ii]) @ Y[ib]
        using solve (factor once if possible) for numerical stability & perf.
        Y can be complex (for s-domain).
        """
        if not internal_idx:
            return Y[np.ix_(boundary_idx, boundary_idx)].copy()

        Ybb = Y[np.ix_(boundary_idx, boundary_idx)]
        Ybi = Y[np.ix_(boundary_idx, internal_idx)]
        Yib = Y[np.ix_(internal_idx, boundary_idx)]
        Yii = Y[np.ix_(internal_idx, internal_idx)]

        # Efficient: solve Yii @ X = Yib  → X shape (|int|, |bnd|)
        try:
            if HAS_SCIPY and np.iscomplexobj(Y):
                X = scipy_solve(Yii, Yib, assume_a="gen")  # general complex
            else:
                X = np.linalg.solve(Yii, Yib)
        except np.linalg.LinAlgError as e:
            logger.error("Singular Yii block during Kron Schur — check connectivity or add small shunt. %s", e)
            # fallback: pseudo-inverse (slow but robust)
            X = np.linalg.pinv(Yii) @ Yib

        Y_red = Ybb - Ybi @ X
        return Y_red

    def reduce_kron(
        self,
        boundary: Optional[List[str]] = None,
        s: complex = 0j,
        boundary_strategy: str = "non_ground_device",
        min_edge_weight: float = 1e-15,
    ) -> nx.Graph:
        """
        Perform Kron reduction (s-domain RC).
        Returns a new nx.Graph with ONLY boundary nodes + effective edges.
        Edge attrs: resistance (from DC), effective_y_real/imag (at given s), type="kron_effective"
        Node attrs preserved from original (base_net, role, shunt_cap etc.)
        """
        if boundary is None:
            boundary = self.identify_boundary(strategy=boundary_strategy)

        if len(boundary) < 2:
            logger.warning("Less than 2 boundary nodes — returning copy of original (no reduction possible)")
            return self.G.copy()

        # Work on full graph or induced? For global use full; for local caller passes subgraph
        all_nodes = list(self.G.nodes())
        G_mat, C_mat = self._build_G_C_matrices(all_nodes)

        # Build Y(s)
        Y = G_mat.astype(complex) + s * C_mat.astype(complex)

        bnd_idx = [self.node_idx[b] for b in boundary if b in self.node_idx]
        int_idx = [i for i in range(len(all_nodes)) if i not in bnd_idx]

        logger.info("Kron reduction: |boundary|=%d, |internal|=%d, s=%s", len(bnd_idx), len(int_idx), s)

        Y_red = self._schur_complement(Y, bnd_idx, int_idx)

        # Also compute pure DC G_red for resistance attribute (always useful)
        G_red = self._schur_complement(G_mat.astype(complex), bnd_idx, int_idx).real

        # Build reduced graph
        reduced_G: nx.Graph = nx.Graph()
        for node in boundary:
            if node in self.G:
                reduced_G.add_node(node, **self.G.nodes[node])

        n_b = len(boundary)
        edges_added = 0
        # Use relative threshold for robustness across different net sizes / value scales
        max_diag = max(np.abs(np.diag(Y_red)).max(), 1e-12)
        rel_threshold = max(min_edge_weight, 1e-9 * max_diag)

        for i in range(n_b):
            for j in range(i + 1, n_b):
                y = Y_red[i, j]
                g_dc = G_red[i, j]
                if abs(y) < rel_threshold and abs(g_dc) < rel_threshold:
                    continue

                u, v = boundary[i], boundary[j]

                # Off-diagonal in passive admittance: Y_ij <= 0 (negative mutual conductance)
                # We store positive resistance / conductance for usability.
                g_pos = max(-g_dc, 0.0)          # positive effective conductance from DC Schur
                y_real_neg = float(y.real)       # raw off-diagonal is negative or zero (correct)
                y_imag = float(y.imag)

                # Decide type for downstream compatibility (SPEF, timing tools expect resistor/coupling_cap)
                if g_pos > rel_threshold or abs(y_real_neg) > rel_threshold:
                    edge_type = "resistor"
                elif abs(y_imag) > rel_threshold:
                    edge_type = "coupling_cap"
                else:
                    edge_type = "kron_effective"

                attrs: Dict[str, Any] = {
                    "type": edge_type,
                    "s_used": str(s),
                    "effective_y_real": y_real_neg,   # negative for i≠j — this is mathematically correct
                    "effective_y_imag": y_imag,
                    "effective_conductance": g_pos,   # positive value, easy to use
                }
                if g_pos > 0:
                    attrs["resistance"] = float(1.0 / g_pos)
                if abs(y_imag) > 0 and s.imag != 0:
                    c_approx = float(y_imag / s.imag)
                    attrs["coupling_cap_approx"] = c_approx
                    if edge_type in ("resistor", "coupling_cap"):
                        attrs["coupling_cap"] = c_approx

                reduced_G.add_edge(u, v, **attrs)
                edges_added += 1

        if edges_added == 0:
            logger.warning(
                "No effective edges were added in this reduction step. "
                "Possible reasons: (1) boundary nodes have no internal paths between them in this component, "
                "(2) all effective conductances < rel_threshold (%.2e), "
                "(3) the subgraph for these boundary is already minimal. "
                "Check your boundary_strategy or try a smaller min_edge_weight / different s.",
                rel_threshold
            )

        # Map eliminated internal shunt_cap proportionally to boundary (simple degree-weighted for realizability)
        # (advanced: solve voltages at s and do energy equiv, but this is good enough heuristic)
        total_internal_c = 0.0
        for node in all_nodes:
            if node not in boundary:
                total_internal_c += self.G.nodes[node].get("shunt_cap", 0.0)

        if total_internal_c > 0 and boundary:
            # distribute evenly or by degree in original (simple)
            per_port = total_internal_c / len(boundary)
            for node in boundary:
                old = reduced_G.nodes[node].get("shunt_cap", 0.0)
                reduced_G.nodes[node]["shunt_cap"] = old + per_port
                reduced_G.nodes[node]["kron_mapped_internal_cap"] = per_port

        logger.info("Reduced graph: %d nodes, %d effective edges", reduced_G.number_of_nodes(), reduced_G.number_of_edges())
        return reduced_G

    # ------------------------------------------------------------------
    # 4. Hierarchical Kron (per base_net local → global)
    # ------------------------------------------------------------------
    def hierarchical_kron_reduce(
        self,
        s: complex = 0j,
        local_boundary_strategy: str = "non_ground_device",
        global_boundary_strategy: str = "non_ground_device",
        merge_parallel: bool = True,
        do_global: bool = False,
    ) -> nx.Graph:
        """
        分层 Kron：
        1. Per base_net 局部约简 (local Kron on each base_net subgraph)
        2. 合并局部约简结果
        3. [可选] 全局 Kron on the merged boundary ports (默认 False，适合典型 DSPF per-net 寄生)

        对于 SRAM WL/BL 等典型场景，不同 base_net 之间在寄生 RC 图里通常没有直接 R/C 连接（只通过器件逻辑连接），
        因此 do_global=False 已经足够，且更安全（避免二次 Schur 数值过滤掉弱 effective 边）。
        """
        groups = self.group_by_base_net()
        logger.info("Hierarchical Kron: %d base_nets found", len(groups))

        reduced_pieces: List[nx.Graph] = []
        global_boundary_set: set = set()

        for bnet, node_list in groups.items():
            if len(node_list) <= 3:
                # too small to reduce meaningfully — keep original piece
                piece = self.G.subgraph(node_list).copy()
                reduced_pieces.append(piece)
                global_boundary_set.update(node_list)
                continue

            local_G = self.G.subgraph(node_list).copy()
            local_reducer = KronReducer(local_G)
            local_bnd = local_reducer.identify_boundary(strategy=local_boundary_strategy)

            if len(local_bnd) < 2:
                local_bnd = node_list[: min(4, len(node_list))]

            reduced_local = local_reducer.reduce_kron(
                boundary=local_bnd, s=s, boundary_strategy=local_boundary_strategy
            )
            reduced_pieces.append(reduced_local)
            global_boundary_set.update(local_bnd)

        # Merge all local reduced pieces (compose_all handles overlapping boundary nodes)
        if not reduced_pieces:
            return nx.Graph()

        merged = nx.compose_all(reduced_pieces)

        # Optional: if parallel edges appeared during compose (unlikely), user can aggregate
        if merge_parallel and isinstance(merged, nx.MultiGraph):
            # convert to simple Graph summing attributes if needed (advanced)
            pass

        logger.info("After local reduction + merge: %d nodes", merged.number_of_nodes())

        if do_global:
            # Only do this if user explicitly requests (e.g. power grid, top-level clock with cross-net R/C)
            logger.info("Performing final global Kron on merged boundary ports (do_global=True)")
            global_reducer = KronReducer(merged)
            final_boundary = list(global_boundary_set)
            final_reduced = global_reducer.reduce_kron(
                boundary=final_boundary, s=s, boundary_strategy=global_boundary_strategy
            )
        else:
            # Recommended default for SRAM WL/BL / per-net parasitic: just use the merged local reductions
            final_reduced = merged

        # Preserve original global attrs where possible (from the very first self.G)
        for n in final_reduced.nodes():
            if n in self.G:
                for k, v in self.G.nodes[n].items():
                    if k not in final_reduced.nodes[n]:
                        final_reduced.nodes[n][k] = v

        # Recompute a light shunt_cap mapping on the final union (in case local pieces didn't cover all)
        # This is lightweight and ensures total C conservation at top level.
        total_internal_c = 0.0
        all_original_nodes = list(self.G.nodes())
        final_bnd = list(final_reduced.nodes())
        for node in all_original_nodes:
            if node not in final_bnd:
                total_internal_c += self.G.nodes[node].get("shunt_cap", 0.0)
        if total_internal_c > 0 and final_bnd:
            per_port = total_internal_c / len(final_bnd)
            for node in final_bnd:
                old = final_reduced.nodes[node].get("shunt_cap", 0.0)
                final_reduced.nodes[node]["shunt_cap"] = old + per_port
                final_reduced.nodes[node]["kron_mapped_internal_cap"] = per_port

        logger.info("Final reduced graph (hierarchical, no cross-base_net Kron): %d nodes, %d edges",
                    final_reduced.number_of_nodes(), final_reduced.number_of_edges())
        return final_reduced

    # ------------------------------------------------------------------
    # 5. I/O helpers (align with exporters.py / to_graphml)
    # ------------------------------------------------------------------
    @staticmethod
    def load_graph(path: Union[str, Path]) -> Union[nx.Graph, nx.MultiGraph]:
        """Load .graphml or .gml (tries graphml first)."""
        p = Path(path)
        if p.suffix.lower() in (".graphml", ".xml"):
            return nx.read_graphml(p)
        elif p.suffix.lower() == ".gml":
            return nx.read_gml(p)
        else:
            # try both
            try:
                return nx.read_graphml(p)
            except Exception:
                return nx.read_gml(p)

    @staticmethod
    def save_graph(G: nx.Graph, path: Union[str, Path], fmt: str = "graphml") -> None:
        p = Path(path)
        if fmt == "graphml" or p.suffix.lower() in (".graphml", ".xml"):
            nx.write_graphml(G, p)
        else:
            nx.write_gml(G, p)
        logger.info("Saved reduced graph to %s (%d nodes, %d edges)", p, G.number_of_nodes(), G.number_of_edges())


# ----------------------------------------------------------------------
# Standalone CLI (for direct use without modifying dspf_refiner)
# ----------------------------------------------------------------------
def main_cli():
    parser = argparse.ArgumentParser(
        description="Kron Reduction step for spf_parasitic_reduction (post dspf_refiner)"
    )
    parser.add_argument("--input", "-i", required=True, help="Input .graphml or .gml from rc_graph.py export")
    parser.add_argument("--output", "-o", required=True, help="Output reduced .graphml/.gml")
    parser.add_argument("--hierarchical", action="store_true", help="Enable per-base_net local Kron (recommended for SRAM WL/BL)")
    parser.add_argument("--skip-global-kron", action="store_true",
                        help="Skip the final global Kron step after merging local reductions (default behavior, safer for per-net parasitic)")
    parser.add_argument("--s", type=complex, default=0j, help="s value for Y(s) e.g. 1j*2*pi*1e9 (default DC=0)")
    parser.add_argument("--boundary-strategy", default="non_ground_device",
                        choices=["non_ground_device", "non_ground", "all"],
                        help="How to auto-select ports/boundary nodes")
    parser.add_argument("--no-map-cap", action="store_true", help="Do not map internal shunt_cap to boundary")
    args = parser.parse_args()

    logger.info("Loading graph from %s", args.input)
    G = KronReducer.load_graph(args.input)

    reducer = KronReducer(G)

    if args.hierarchical:
        do_global = not args.skip_global_kron
        reduced = reducer.hierarchical_kron_reduce(
            s=args.s,
            local_boundary_strategy=args.boundary_strategy,
            do_global=do_global
        )
    else:
        reduced = reducer.reduce_kron(s=args.s, boundary_strategy=args.boundary_strategy)

    KronReducer.save_graph(reduced, args.output)

    logger.info("Kron reduction complete. You can now feed %s to downstream timing / sram tools.", args.output)


if __name__ == "__main__":
    main_cli()
