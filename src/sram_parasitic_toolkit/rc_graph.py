"""
RC Graph Engine (Stage 1 core) - Corrected for GraphML compatibility.

Key fixes:
- Only scalar + simple dict attributes on nodes/edges for full GraphML support.
- Removed complex nested structures (full parameters, device_load lists) from the graph itself.
- Rich data (device_load, full parameters, hierarchical details) is still available via query methods.
- TC1/TC2 fully supported on resistor edges.
"""

from __future__ import annotations

from typing import Dict, List, Any, Optional

import networkx as nx

from .hier_node import parse_hier_node, HierNode
from .sram_semantics import infer_sram_role
from netlist_parser import Netlist, DeviceInstance


class RCGraphBuilder:
    """
    Constructs a clean RC parasitic graph compatible with GraphML export.

    The graph stores:
    - Nodes: base_net, shunt_cap, is_ground
    - Edges (R): resistance, tc1, tc2, type="resistor"
    - Edges (C coupling): coupling_cap, type="coupling_cap"

    All rich semantic data (full device_load, original parameters, SRAM role)
    is computed on-demand in the query methods to keep the graph lightweight
    and GraphML-serializable.
    """

    def __init__(self, netlist: Netlist):
        self.netlist = netlist
        self.G: nx.MultiGraph = nx.MultiGraph()
        self.node_to_hier: Dict[str, HierNode] = {}
        self.net_to_nodes: Dict[str, List[str]] = {}
        # Separate structure for device load (not attached to graph for GraphML safety)
        self._device_load: Dict[str, List[Dict[str, Any]]] = {}
        self._build_graph()

    def _build_graph(self) -> None:
        for cell in self.netlist.cells:
            for inst in cell.instances:
                self._process_instance(inst, cell.name)

    def _process_instance(self, inst: DeviceInstance, cell_name: str) -> None:
        role = infer_sram_role(inst.device_name, inst.parameters, inst.nodes)

        # Register nodes with minimal attributes (GraphML safe)
        for raw_node in inst.nodes:
            if raw_node not in self.node_to_hier:
                hier = parse_hier_node(raw_node)
                self.node_to_hier[raw_node] = hier
                base = hier.base_net
                if base not in self.net_to_nodes:
                    self.net_to_nodes[base] = []
                if raw_node not in self.net_to_nodes[base]:
                    self.net_to_nodes[base].append(raw_node)

                if not self.G.has_node(raw_node):
                    self.G.add_node(
                        raw_node,
                        base_net=base,
                        shunt_cap=0.0,
                        is_ground=hier.is_ground,
                    )

        # Handle device load separately (rich data)
        if inst.code not in ("r", "c"):
            for raw_node in inst.nodes:
                if raw_node not in self._device_load:
                    self._device_load[raw_node] = []
                self._device_load[raw_node].append({
                    "name": inst.name,
                    "device_name": inst.device_name,
                    "role": role,
                    "cell": cell_name,
                    "parameters": dict(inst.parameters),  # copy
                })

        if inst.code == "r":
            self._add_resistor_edge(inst, role)
        elif inst.code == "c":
            self._add_capacitor_edge(inst, role)

    def _add_resistor_edge(self, inst: DeviceInstance, role: str) -> None:
        if len(inst.nodes) < 2:
            return
        n1, n2 = inst.nodes[0], inst.nodes[1]
        try:
            r_val = float(inst.number or inst.parameters.get("R", inst.parameters.get("r", 0)))
        except (ValueError, TypeError):
            r_val = 0.0

        tc1 = float(inst.parameters.get("TC1", inst.parameters.get("tc1", 0.0)))
        tc2 = float(inst.parameters.get("TC2", inst.parameters.get("tc2", 0.0)))

        self.G.add_edge(
            n1, n2,
            key=inst.name,
            type="resistor",
            resistance=r_val,
            tc1=tc1,
            tc2=tc2,
            device=inst.device_name or "",
            role=role,
        )

    def _add_capacitor_edge(self, inst: DeviceInstance, role: str) -> None:
        if len(inst.nodes) < 2:
            return
        n1, n2 = inst.nodes[0], inst.nodes[1]
        try:
            c_val = float(inst.number or inst.parameters.get("C", inst.parameters.get("c", 0)))
        except (ValueError, TypeError):
            c_val = 0.0

        hier2 = self.node_to_hier.get(n2)
        if hier2 and hier2.is_ground:
            if self.G.has_node(n1):
                current = self.G.nodes[n1].get("shunt_cap", 0.0)
                self.G.nodes[n1]["shunt_cap"] = current + c_val
        else:
            self.G.add_edge(
                n1, n2,
                key=inst.name,
                type="coupling_cap",
                coupling_cap=c_val,
                device=inst.device_name or "",
                role=role,
            )

    # ------------------------- Public Query APIs -------------------------

    def get_node2node_subgraph(self, nodes: List[str]) -> nx.MultiGraph:
        valid = [n for n in nodes if self.G.has_node(n)]
        return self.G.subgraph(valid).copy()

    def get_rc_ladder_for_net(self, net: str, include_tc: bool = True) -> List[Dict[str, Any]]:
        elements: List[Dict[str, Any]] = []
        nodes = self.net_to_nodes.get(net, [])

        for n in nodes:
            # Resistor and coupling edges
            for nbr in list(self.G.neighbors(n)):
                for key, data in self.G.get_edge_data(n, nbr).items():
                    elem: Dict[str, Any] = {
                        "from": n,
                        "to": nbr,
                        "name": key,
                        "type": data.get("type"),
                    }
                    if data.get("type") == "resistor":
                        elem["resistance"] = data.get("resistance")
                        if include_tc:
                            elem["tc1"] = data.get("tc1", 0.0)
                            elem["tc2"] = data.get("tc2", 0.0)
                    elif data.get("type") == "coupling_cap":
                        elem["coupling_cap"] = data.get("coupling_cap")
                    elements.append(elem)

            # Shunt capacitance
            shunt = self.G.nodes[n].get("shunt_cap", 0.0)
            if shunt > 0:
                elements.append({
                    "from": n,
                    "to": "ground",
                    "type": "shunt_cap",
                    "shunt_cap": shunt,
                })

        return elements

    def compute_net2net_totals(self, net: str) -> Dict[str, Any]:
        total_res = 0.0
        coupling: Dict[str, float] = {}
        shunt_total = 0.0

        nodes = self.net_to_nodes.get(net, [])
        for n in nodes:
            shunt_total += self.G.nodes[n].get("shunt_cap", 0.0)
            for nbr in self.G.neighbors(n):
                nbr_base = self.node_to_hier.get(nbr, HierNode(raw=nbr, base_net=nbr)).base_net
                for data in self.G.get_edge_data(n, nbr).values():
                    if data.get("type") == "resistor":
                        total_res += float(data.get("resistance", 0.0))
                    elif data.get("type") == "coupling_cap":
                        val = float(data.get("coupling_cap", 0.0))
                        coupling[nbr_base] = coupling.get(nbr_base, 0.0) + val

        return {
            "target_net": net,
            "total_series_resistance_ohm": total_res,
            "total_shunt_capacitance_f": shunt_total,
            "coupling_capacitance_by_net_f": coupling,
            "num_raw_nodes": len(nodes),
        }

    def get_device_load_for_net(self, net: str) -> List[Dict[str, Any]]:
        """Return device load using the separate rich structure."""
        result = []
        for raw_node in self.net_to_nodes.get(net, []):
            result.extend(self._device_load.get(raw_node, []))
        return result

    def to_graphml(self, path: str) -> None:
        """Export clean graph. All attributes are GraphML-compatible primitives."""
        nx.write_graphml(self.G, path)

    def get_summary_stats(self) -> Dict[str, Any]:
        return {
            "num_nodes": self.G.number_of_nodes(),
            "num_edges": self.G.number_of_edges(),
            "num_resistor_edges": sum(1 for _, _, d in self.G.edges(data=True) if d.get("type") == "resistor"),
            "num_coupling_edges": sum(1 for _, _, d in self.G.edges(data=True) if d.get("type") == "coupling_cap"),
            "total_shunt_cap_f": sum(float(d.get("shunt_cap", 0)) for _, d in self.G.nodes(data=True)),
        }
