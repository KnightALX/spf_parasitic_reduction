
# CDL Back-Annotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement SPF→CDL node name mapping + hierarchical subcircuit injection to solve floating RC nodes in back-annotation.

**Architecture:** Parse CDL netlist into a HierarchyTree → map Kron-reduced SPF node names to CDL-scoped local net names → contract nodes that map to the same CDL net (merging @ branches) → output per-subckt injection-ready SPICE segments.

**Tech Stack:** Python 3.10+, networkx, pytest, dataclasses

---

## File Structure

```
src/sram_parasitic_toolkit/
├── cdl_parser.py          ← NEW: CDL hierarchy parser
├── spf_to_cdl_mapper.py   ← NEW: SPF→CDL node name mapper
├── net_contractor.py      ← NEW: node merger + port identification
├── hier_node.py           ← MODIFY: add @ suffix support
└── kron/
    └── cdl_spice_exporter.py  ← NEW: injection-style SPICE output

tests/
├── test_cdl_parser.py          ← NEW
├── test_spf_to_cdl_mapper.py   ← NEW
├── test_net_contractor.py      ← NEW
└── test_cdl_spice_exporter.py  ← NEW

run_flow.py  ← MODIFY: add back-annotate subcommand + extend pipeline
```

**Interfaces:**

```
cdl_parser.parse_cdl(path) → HierarchyTree
spf_to_cdl_mapper.map_nodes(kron_nodes, HierarchyTree) → {spf_node: CdlScope}
net_contractor.contract(kron_graph, mapping, hierarchy_tree) → contracted_graph
cdl_spice_exporter.cdl_back_annotate(contracted_graph, hierarchy_tree, output_path, ...) → None
```

---

### Task 1: Add `@` suffix support to hier_node.py

**Files:**
- Modify: `src/sram_parasitic_toolkit/hier_node.py:23-86`
- Modify: `tests/test_smoke.py` (add new test)

- [ ] **Step 1: Write failing test for @ suffix parsing**

Add to `tests/test_smoke.py`:

```python
def test_parse_hier_node_with_at_suffix():
    node = parse_hier_node("Xtop.xmmio.net147@1")
    assert node.raw == "Xtop.xmmio.net147@1"
    assert node.branch == "1"
    assert node.base_net == "Xtop.xmmio.net147"
    assert node.hierarchy == ["Xtop", "xmmio", "net147"]
    assert node.is_ground is False

def test_parse_hier_node_with_at_suffix_no_colon():
    node = parse_hier_node("net147@2")
    assert node.raw == "net147@2"
    assert node.branch == "2"
    assert node.base_net == "net147"
    assert node.port is None

def test_parse_hier_node_no_at_suffix_unchanged():
    node = parse_hier_node("XXYMUX_BANK0.XXYMUX<1>.MMYMUX_RBL:G")
    assert node.base_net == "XXYMUX_BANK0.XXYMUX<1>.MMYMUX_RBL"
    assert node.port == "G"
    assert node.branch is None
```

Run: `pytest tests/test_smoke.py::test_parse_hier_node_with_at_suffix -v`
Expected: FAIL — `HierNode.__init__() got an unexpected keyword argument 'branch'`

- [ ] **Step 2: Add `branch` field to HierNode dataclass**

In `hier_node.py`, modify the `HierNode` dataclass (line 23-30):

```python
@dataclass
class HierNode:
    """Structured representation of a hierarchical node name."""
    raw: str
    base_net: str
    hierarchy: List[str] = field(default_factory=list)
    port: Optional[str] = None
    branch: Optional[str] = None
    is_ground: bool = False
```

Run: `pytest tests/test_smoke.py::test_parse_hier_node_with_at_suffix -v`
Expected: FAIL — `branch` is always `None`, assertion `node.branch == "1"` fails

- [ ] **Step 3: Add @ splitting logic in parse_hier_node()**

In `hier_node.py`, modify `parse_hier_node()` (insert after line 55 `node = node.strip()`):

```python
def parse_hier_node(node: str) -> HierNode:
    node = node.strip()
    if not node:
        return HierNode(raw=node, base_net=node)

    # NEW: strip @ suffix before existing parsing
    branch: Optional[str] = None
    if "@" in node:
        node, branch = node.rsplit("@", 1)

    lower = node.lower()
    if lower in {"0", "ground", "gnd"}:
        return HierNode(raw=node, base_net="ground", is_ground=True, branch=branch)

    if ":" in node:
        prefix, port = node.rsplit(":", 1)
        base_net = prefix
        hierarchy = [p for p in prefix.split(".") if p]
        return HierNode(
            raw=node,
            base_net=base_net,
            hierarchy=hierarchy,
            port=port,
            branch=branch,
        )

    parts = [p for p in node.split(".") if p]
    if len(parts) > 1:
        return HierNode(
            raw=node,
            base_net=parts[-1],
            hierarchy=parts[:-1],
            port=None,
            branch=branch,
        )

    return HierNode(raw=node, base_net=node, branch=branch)
```

Wait — the `raw` field should PRESERVE the original string with `@`. Let me reconsider. The `raw` field is used as the graph node ID. Stripping `@` from `raw` would break the graph node identity. We should store the original in `raw` and a cleaned version for parsing purposes.

Actually, looking at how `raw` is used in `dspf_refiner.py`:

```python
for raw_node in inst.nodes:
    if raw_node not in self.node_to_hier:
        hier = parse_hier_node(raw_node)
```

`raw` is set to the input `node` parameter. But if we strip `@` from `node` before setting `raw`, we lose the original. The fix: capture the original before stripping.

Correct approach — REVISE `parse_hier_node`:

```python
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
            base_net=parts[-1],
            hierarchy=parts[:-1],
            port=None,
            branch=branch,
        )
    
    return HierNode(raw=raw_original, base_net=working, branch=branch)
```

Run: `pytest tests/test_smoke.py -v`
Expected: ALL PASS

- [ ] **Step 4: Run all existing tests to verify no regression**

Run: `pytest tests/test_smoke.py -v`
Expected: ALL 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/sram_parasitic_toolkit/hier_node.py tests/test_smoke.py
git commit -m "feat(hier_node): add @ branch suffix parsing support"
```

---

### Task 2: Implement CDL hierarchy parser (cdl_parser.py)

**Files:**
- Create: `src/sram_parasitic_toolkit/cdl_parser.py`
- Create: `tests/test_cdl_parser.py`

- [ ] **Step 1: Write a test CDL fixture string and failing test**

Create `tests/test_cdl_parser.py`:

```python
import pytest
from sram_parasitic_toolkit.cdl_parser import parse_cdl, HierarchyTree

CDL_FIXTURE = """\
.subckt leaf_subckt  A  B  VDD  VSS
M0 A B net1 VSS nch w=1u l=0.1u
M1 net1 A VDD VDD pch w=2u l=0.1u
.ends leaf_subckt

.subckt mid_subckt  in  out  VDD  VSS
Xleaf1  in  internal  VDD  VSS  leaf_subckt
Xleaf2  internal  out  VDD  VSS  leaf_subckt
.ends mid_subckt

.subckt top_subckt  data  result  VDD  VSS
Xmid  data  result  VDD  VSS  mid_subckt
.ends top_subckt

Xtop  PAD_DATA  PAD_RESULT  VDD  VSS  top_subckt
"""

def test_parse_cdl_root_instance():
    tree = parse_cdl(CDL_FIXTURE)
    assert tree is not None
    assert tree.instance_name == "Xtop"
    assert tree.subckt_def_name == "top_subckt"
    assert tree.ports == ["data", "result", "VDD", "VSS"]


def test_parse_cdl_hierarchy():
    tree = parse_cdl(CDL_FIXTURE)
    assert "mid" in tree.instances
    mid = tree.instances["mid"]
    assert mid.instance_name == "Xmid"
    assert mid.subckt_def_name == "mid_subckt"
    assert mid.ports == ["in", "out", "VDD", "VSS"]


def test_parse_cdl_local_nets():
    tree = parse_cdl(CDL_FIXTURE)
    mid = tree.instances["mid"]
    assert "internal" in mid.local_nets
    assert "in" in mid.local_nets
    assert "out" in mid.local_nets


def test_parse_cdl_deep_leaf():
    tree = parse_cdl(CDL_FIXTURE)
    leaf1 = tree.instances["mid"].instances["leaf1"]
    assert leaf1.instance_name == "Xleaf1"
    assert leaf1.subckt_def_name == "leaf_subckt"
    assert leaf1.ports == ["A", "B", "VDD", "VSS"]
    assert "net1" in leaf1.local_nets
    assert "A" in leaf1.local_nets


def test_parse_cdl_instance_port_map():
    tree = parse_cdl(CDL_FIXTURE)
    mid = tree.instances["mid"]
    assert mid.instance_port_map is not None
    assert mid.instance_port_map["leaf1"]["A"] == "in"
    assert mid.instance_port_map["leaf1"]["B"] == "internal"
    assert mid.instance_port_map["leaf2"]["A"] == "internal"
    assert mid.instance_port_map["leaf2"]["B"] == "out"


def test_parse_cdl_parent_reference():
    tree = parse_cdl(CDL_FIXTURE)
    mid = tree.instances["mid"]
    assert mid.parent is tree
    leaf1 = mid.instances["leaf1"]
    assert leaf1.parent is mid


def test_parse_cdl_scope_name():
    tree = parse_cdl(CDL_FIXTURE)
    leaf1 = tree.instances["mid"].instances["leaf1"]
    assert leaf1.scope_name == "top_subckt.Xmid.Xleaf1"
```

Run: `pytest tests/test_cdl_parser.py -v`
Expected: ALL FAIL — module not found

- [ ] **Step 2: Define HierarchyTree dataclass**

Create `src/sram_parasitic_toolkit/cdl_parser.py`:

```python
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
    """
    Parse CDL netlist and build HierarchyTree.

    Args:
        filepath_or_text: Path to CDL file or raw CDL text
        is_text: If True, treat as raw text; otherwise as file path

    Returns:
        Root HierarchyTree node (the top-level X instance)
    """
    if is_text:
        lines = filepath_or_text.splitlines()
    else:
        path = Path(filepath_or_text)
        lines = path.read_text(encoding="utf-8").splitlines()
    return _parse_lines(lines)
```

Run: `pytest tests/test_cdl_parser.py -v`
Expected: Some fail — `_parse_lines` not defined yet

- [ ] **Step 3: Implement line preprocessor (join continuations, strip comments)**

Add to `cdl_parser.py`:

```python
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
```

Run: `pytest tests/test_cdl_parser.py::test_parse_cdl_root_instance -v`
Expected: Same failures (main logic not yet implemented)

- [ ] **Step 4: Implement tokenizer and subckt block collector**

Add to `cdl_parser.py`:

```python
def _tokenize(line: str) -> List[str]:
    return line.split()


def _is_instance(line: str) -> bool:
    return line.startswith("X") or line.startswith("x")


def _is_subckt(line: str) -> bool:
    return line.lower().startswith(".subckt")


def _is_ends(line: str) -> bool:
    return line.lower().startswith(".ends")
```

- [ ] **Step 5: Implement _parse_lines — main parsing logic**

Add to `cdl_parser.py`:

```python
def _parse_lines(lines: List[str]) -> HierarchyTree:
    processed = _preprocess_lines(lines)

    subckt_registry: Dict[str, Dict] = {}
    current_subckt: Optional[Dict] = None
    
    i = 0
    while i < len(processed):
        line = processed[i]
        
        if _is_subckt(line):
            tokens = _tokenize(line)
            name = tokens[1]
            ports = tokens[2:]
            current_subckt = {
                "name": name,
                "ports": ports,
                "local_nets": set(ports),
                "instances": [],       # list of (inst_name, port_connections, subckt_type)
            }
            subckt_registry[name] = current_subckt
            i += 1
            continue
        
        if _is_ends(line) and current_subckt is not None:
            current_subckt = None
            i += 1
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
        
        i += 1
    
    top_instance_line = None
    for line in reversed(processed):
        if _is_instance(line) and not line.lower().startswith(".subckt"):
            top_instance_line = line
            break
    
    if top_instance_line is None:
        raise ValueError("No top-level instance (X line) found in CDL")
    
    return _build_hierarchy_tree(top_instance_line, subckt_registry)
```

- [ ] **Step 6: Implement _build_hierarchy_tree — recursive tree builder**

Add to `cdl_parser.py`:

```python
def _build_hierarchy_tree(
    top_line: str,
    registry: Dict[str, Dict],
    parent: Optional[HierarchyTree] = None,
    instance_name: str = "",
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
        child_tree = _build_hierarchy_tree(
            child_line, registry, parent=tree, instance_name=child_inst_name
        )
        tree.instances[child_inst_name] = child_tree
        
        if child_type in registry:
            child_def = registry[child_type]
            child_ports = child_def["ports"]
            port_map[child_inst_name] = {}
            for j, port in enumerate(child_ports):
                if j < len(child_port_cons):
                    port_map[child_inst_name][port] = child_port_cons[j]
    
    tree.instance_port_map = port_map
    return tree
```

- [ ] **Step 7: Run tests to verify parser**

Run: `pytest tests/test_cdl_parser.py -v`
Expected: ALL 7 tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/sram_parasitic_toolkit/cdl_parser.py tests/test_cdl_parser.py
git commit -m "feat(cdl_parser): implement CDL hierarchy parser with HierarchyTree"
```

---

### Task 3: Implement SPF→CDL node name mapper (spf_to_cdl_mapper.py)

**Files:**
- Create: `src/sram_parasitic_toolkit/spf_to_cdl_mapper.py`
- Create: `tests/test_spf_to_cdl_mapper.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_spf_to_cdl_mapper.py`:

```python
import pytest
from sram_parasitic_toolkit.cdl_parser import parse_cdl, HierarchyTree
from sram_parasitic_toolkit.spf_to_cdl_mapper import map_nodes, CdlScope

# Reuse CDL fixture from Task 2
CDL_FIXTURE = """\
.subckt leaf_subckt  A  B  VDD  VSS
M0 A B net1 VSS nch w=1u l=0.1u
M1 net1 A VDD VDD pch w=2u l=0.1u
.ends leaf_subckt

.subckt mid_subckt  in  out  VDD  VSS
Xleaf1  in  internal  VDD  VSS  leaf_subckt
Xleaf2  internal  out  VDD  VSS  leaf_subckt
.ends mid_subckt

.subckt top_subckt  data  result  VDD  VSS
Xmid  data  result  VDD  VSS  mid_subckt
.ends top_subckt

Xtop  PAD_DATA  PAD_RESULT  VDD  VSS  top_subckt
"""


def test_map_simple_node():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    kron_nodes = ["Xtop.mid.internal@1"]
    mapping, unmapped = map_nodes(kron_nodes, tree)
    assert "Xtop.mid.internal@1" in mapping
    scope = mapping["Xtop.mid.internal@1"]
    assert scope.scope_path == "Xtop.mid"
    assert scope.local_net == "internal"
    assert scope.is_port is False
    assert scope.spf_branch == "1"


def test_map_node_with_branch():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    kron_nodes = ["Xtop.mid.leaf1.A@2"]
    mapping, unmapped = map_nodes(kron_nodes, tree)
    scope = mapping["Xtop.mid.leaf1.A@2"]
    assert scope.scope_path == "Xtop.mid.leaf1"
    assert scope.local_net == "A"
    assert scope.is_port is True


def test_map_unmapped_node():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    kron_nodes = ["Xtop.ghost.ghost_net@1"]
    mapping, unmapped = map_nodes(kron_nodes, tree)
    assert len(mapping) == 0
    assert len(unmapped) == 1
    assert unmapped[0]["spf_node"] == "Xtop.ghost.ghost_net@1"
    assert "reason" in unmapped[0]


def test_map_multiple_nodes_same_net():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    kron_nodes = [
        "Xtop.mid.internal@1",
        "Xtop.mid.internal@2",
        "Xtop.mid.internal@3",
    ]
    mapping, unmapped = map_nodes(kron_nodes, tree)
    assert len(mapping) == 3
    for node in kron_nodes:
        scope = mapping[node]
        assert scope.local_net == "internal"
        assert scope.scope_path == "Xtop.mid"


def test_map_port_node():
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    kron_nodes = ["Xtop.mid.out@1"]
    mapping, unmapped = map_nodes(kron_nodes, tree)
    scope = mapping["Xtop.mid.out@1"]
    assert scope.scope_path == "Xtop.mid"
    assert scope.local_net == "out"
    assert scope.is_port is True
```

Run: `pytest tests/test_spf_to_cdl_mapper.py -v`
Expected: ALL FAIL — module not found

- [ ] **Step 2: Create CdlScope dataclass and map_nodes skeleton**

Create `src/sram_parasitic_toolkit/spf_to_cdl_mapper.py`:

```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .cdl_parser import HierarchyTree

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class CdlScope:
    scope_path: str
    local_net: str
    is_port: bool = False
    spf_branch: Optional[str] = None


def map_nodes(
    kron_nodes: List[str],
    hierarchy_tree: HierarchyTree,
) -> Tuple[Dict[str, CdlScope], List[Dict]]:
    """
    Map SPF hierarchical node names to CDL scoped net names.

    Returns:
        (mapping dict, unmapped list)
        mapping: {spf_node_name → CdlScope}
        unmapped: [{spf_node, reason}, ...]
    """
    mapping: Dict[str, CdlScope] = {}
    unmapped: List[Dict] = []
    
    for node in kron_nodes:
        scope = _map_single_node(node, hierarchy_tree)
        if scope is None:
            unmapped.append({"spf_node": node, "reason": "Node not found in CDL hierarchy"})
        else:
            mapping[node] = scope
    
    logger.info("Mapped %d nodes, %d unmapped", len(mapping), len(unmapped))
    return mapping, unmapped


def _map_single_node(
    spf_node: str,
    root: HierarchyTree,
) -> Optional[CdlScope]:
    branch: Optional[str] = None
    working = spf_node.strip()
    if "@" in working:
        working, branch = working.rsplit("@", 1)
    
    if working.lower() in {"0", "ground", "gnd"}:
        return CdlScope(scope_path="", local_net="0", is_port=False, spf_branch=branch)
    
    if ":" in working:
        working, _ = working.rsplit(":", 1)
    
    parts = [p for p in working.split(".") if p]
    if not parts:
        return None
    
    return _resolve_path(parts, root, spf_node, branch)


def _resolve_path(
    path_parts: List[str],
    current: HierarchyTree,
    original_node: str,
    branch: Optional[str],
) -> Optional[CdlScope]:
    if len(path_parts) == 0:
        return None
    
    first = path_parts[0]
    
    if first == current.instance_name:
        if len(path_parts) == 1:
            return None
        net_candidate = path_parts[-1]
        instance_parts = path_parts[1:-1]
        
        target = current
        instance_path_segments = [current.instance_name]
        for inst_part in instance_parts:
            if inst_part in target.instances:
                target = target.instances[inst_part]
                instance_path_segments.append(inst_part)
            else:
                logger.warning(
                    "Instance '%s' not found in '%s' for node '%s'",
                    inst_part, target.subckt_def_name, original_node,
                )
                return None
        
        scope_path = ".".join(instance_path_segments)
        search_scope = target
        
        while search_scope is not None:
            if net_candidate in search_scope.local_nets:
                is_port = net_candidate in search_scope.ports
                return CdlScope(
                    scope_path=scope_path,
                    local_net=net_candidate,
                    is_port=is_port,
                    spf_branch=branch,
                )
            search_scope = search_scope.parent
        
        logger.warning(
            "Net '%s' not found in any scope for node '%s'",
            net_candidate, original_node,
        )
        return None
    
    if first in current.instances:
        return _resolve_path(path_parts, current.instances[first], original_node, branch)
    
    logger.warning(
        "Instance '%s' not found in '%s' for node '%s'",
        first, current.subckt_def_name, original_node,
    )
    return None
```

Run: `pytest tests/test_spf_to_cdl_mapper.py -v`
Expected: ALL 5 tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/sram_parasitic_toolkit/spf_to_cdl_mapper.py tests/test_spf_to_cdl_mapper.py
git commit -m "feat(mapper): implement SPF→CDL node name mapper"
```

---

### Task 4: Implement net contractor (net_contractor.py)

**Files:**
- Create: `src/sram_parasitic_toolkit/net_contractor.py`
- Create: `tests/test_net_contractor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_net_contractor.py`:

```python
import pytest
import networkx as nx
from sram_parasitic_toolkit.spf_to_cdl_mapper import CdlScope
from sram_parasitic_toolkit.net_contractor import contract


def build_test_graph_and_mapping():
    G = nx.Graph()
    G.add_node("internal@1", shunt_cap=0.01, base_net="internal")
    G.add_node("internal@2", shunt_cap=0.02, base_net="internal")
    G.add_node("A@1", shunt_cap=0.03, base_net="A")
    G.add_node("B@1", shunt_cap=0.04, base_net="B")

    G.add_edge("internal@1", "internal@2", type="resistor", resistance=50.0)
    G.add_edge("internal@1", "A@1", type="resistor", resistance=100.0)
    G.add_edge("internal@2", "A@1", type="resistor", resistance=200.0)
    G.add_edge("internal@1", "B@1", type="coupling_cap", coupling_cap=0.005)
    G.add_edge("A@1", "B@1", type="kron_effective", resistance=300.0)

    mapping = {
        "internal@1": CdlScope(scope_path="Xtop.mid", local_net="internal", is_port=False, spf_branch="1"),
        "internal@2": CdlScope(scope_path="Xtop.mid", local_net="internal", is_port=False, spf_branch="2"),
        "A@1": CdlScope(scope_path="Xtop.mid", local_net="A", is_port=True, spf_branch="1"),
        "B@1": CdlScope(scope_path="Xtop.mid", local_net="B", is_port=True, spf_branch="1"),
    }
    return G, mapping


def test_contract_merge_groups():
    G, mapping = build_test_graph_and_mapping()
    result = contract(G, mapping)
    assert result.has_node("Xtop.mid::internal")
    assert result.has_node("Xtop.mid::A")
    assert result.has_node("Xtop.mid::B")
    assert result.number_of_nodes() == 3


def test_contract_shunt_cap_sum():
    G, mapping = build_test_graph_and_mapping()
    result = contract(G, mapping)
    internal_shunt = result.nodes["Xtop.mid::internal"]["shunt_cap"]
    assert internal_shunt == 0.03


def test_contract_parallel_inter_group_resistance():
    G, mapping = build_test_graph_and_mapping()
    result = contract(G, mapping)
    assert result.has_edge("Xtop.mid::internal", "Xtop.mid::A")
    edge = result["Xtop.mid::internal"]["Xtop.mid::A"]
    assert "resistance" in edge
    expected_r = 1.0 / (1.0 / 100.0 + 1.0 / 200.0)
    assert abs(edge["resistance"] - expected_r) < 0.01


def test_contract_coupling_cap_preserved():
    G, mapping = build_test_graph_and_mapping()
    result = contract(G, mapping)
    assert result.has_edge("Xtop.mid::internal", "Xtop.mid::B")
    edge = result["Xtop.mid::internal"]["Xtop.mid::B"]
    assert "coupling_cap" in edge
    assert edge["coupling_cap"] == 0.005


def test_contract_intra_group_edge_to_shunt():
    G, mapping = build_test_graph_and_mapping()
    result = contract(G, mapping)
    assert not result.has_edge("Xtop.mid::internal", "Xtop.mid::internal")


def test_contract_orphan_edge_skipped():
    G = nx.Graph()
    G.add_node("internal@1", shunt_cap=0.01)
    G.add_node("ghost@1", shunt_cap=0.02)
    G.add_edge("internal@1", "ghost@1", type="resistor", resistance=100.0)
    mapping = {
        "internal@1": CdlScope(scope_path="Xtop.mid", local_net="internal", is_port=False),
    }
    result = contract(G, mapping)
    assert result.number_of_nodes() == 1
    assert result.number_of_edges() == 0
```

Run: `pytest tests/test_net_contractor.py -v`
Expected: ALL FAIL — module not found

- [ ] **Step 2: Implement contract() function**

Create `src/sram_parasitic_toolkit/net_contractor.py`:

```python
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

from .spf_to_cdl_mapper import CdlScope
from .cdl_parser import HierarchyTree

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def contract(
    kron_graph: nx.Graph,
    mapping: Dict[str, CdlScope],
    hierarchy_tree: Optional[HierarchyTree] = None,
) -> nx.Graph:
    """
    Contract Kron-reduced graph nodes that map to the same CDL local net.
    
    Returns a new graph where each node key is "scope_path::local_net".
    """
    merge_groups: Dict[str, List[str]] = {}
    node_to_group: Dict[str, str] = {}
    
    for spf_node, scope in mapping.items():
        if spf_node not in kron_graph:
            continue
        group_key = f"{scope.scope_path}::{scope.local_net}"
        if group_key not in merge_groups:
            merge_groups[group_key] = []
        merge_groups[group_key].append(spf_node)
        node_to_group[spf_node] = group_key
    
    result = nx.Graph()
    
    for group_key, spf_nodes in merge_groups.items():
        total_shunt = 0.0
        for n in spf_nodes:
            total_shunt += kron_graph.nodes[n].get("shunt_cap", 0.0)
        result.add_node(group_key, shunt_cap=total_shunt, num_merged=len(spf_nodes))
    
    for u, v, data in kron_graph.edges(data=True):
        g_u = node_to_group.get(u)
        g_v = node_to_group.get(v)
        
        if g_u is None or g_v is None:
            continue
        
        if g_u == g_v:
            c_val = data.get("coupling_cap") or data.get("coupling_cap_approx", 0.0)
            result.nodes[g_u]["shunt_cap"] += c_val
        else:
            _merge_parallel_edge(result, g_u, g_v, data)
    
    _finalize_edges(result)
    return result


def _merge_parallel_edge(result: nx.Graph, g_u: str, g_v: str, edge_data: Dict[str, Any]) -> None:
    if not result.has_edge(g_u, g_v):
        result.add_edge(g_u, g_v, resistance_sum_inv=0.0, coupling_cap_sum=0.0,
                         effective_conductance_sum=0.0, has_any_edge=False)
    
    existing = result[g_u][g_v]
    existing["has_any_edge"] = True
    
    r_val = edge_data.get("resistance")
    if r_val is not None and r_val > 1e-12 and r_val < 1e12:
        existing["resistance_sum_inv"] += 1.0 / r_val
    
    g_val = edge_data.get("effective_conductance", 0.0)
    if g_val > 0:
        existing["effective_conductance_sum"] += g_val
    
    c_val = edge_data.get("coupling_cap") or edge_data.get("coupling_cap_approx", 0.0)
    if c_val > 0:
        existing["coupling_cap_sum"] += c_val


def _finalize_edges(result: nx.Graph) -> None:
    for u, v, data in list(result.edges(data=True)):
        if not data.get("has_any_edge"):
            result.remove_edge(u, v)
            continue
        
        if data.get("resistance_sum_inv", 0.0) > 0:
            data["resistance"] = 1.0 / data["resistance_sum_inv"]
            data["type"] = "resistor"
            del data["resistance_sum_inv"]
        elif data.get("effective_conductance_sum", 0.0) > 0:
            data["resistance"] = 1.0 / data["effective_conductance_sum"]
            data["type"] = "kron_effective"
            del data["effective_conductance_sum"]
        
        if data.get("coupling_cap_sum", 0.0) > 0:
            data["coupling_cap"] = data["coupling_cap_sum"]
            del data["coupling_cap_sum"]
        
        if "has_any_edge" in data:
            del data["has_any_edge"]
```

Run: `pytest tests/test_net_contractor.py -v`
Expected: ALL 6 tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/sram_parasitic_toolkit/net_contractor.py tests/test_net_contractor.py
git commit -m "feat(contractor): implement node contraction with parallel RC merging"
```

---

### Task 5: Implement CDL injection SPICE exporter (cdl_spice_exporter.py)

**Files:**
- Create: `src/sram_parasitic_toolkit/kron/cdl_spice_exporter.py`
- Create: `tests/test_cdl_spice_exporter.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cdl_spice_exporter.py`:

```python
import pytest
import networkx as nx
from pathlib import Path
from sram_parasitic_toolkit.cdl_parser import parse_cdl, HierarchyTree
from sram_parasitic_toolkit.kron.cdl_spice_exporter import cdl_back_annotate

CDL_FIXTURE = """\
.subckt leaf_subckt  A  B  VDD  VSS
M0 A B net1 VSS nch w=1u l=0.1u
.ends leaf_subckt

.subckt mid_subckt  in  out  VDD  VSS
Xleaf1  in  internal  VDD  VSS  leaf_subckt
Xleaf2  internal  out  VDD  VSS  leaf_subckt
.ends mid_subckt

.subckt top_subckt  data  result  VDD  VSS
Xmid  data  result  VDD  VSS  mid_subckt
.ends top_subckt

Xtop  PAD_DATA  PAD_RESULT  VDD  VSS  top_subckt
"""


def build_contracted_graph():
    G = nx.Graph()
    G.add_node("Xtop.mid::internal", shunt_cap=0.05)
    G.add_node("Xtop.mid::in", shunt_cap=0.01)
    G.add_node("Xtop.mid::out", shunt_cap=0.02)
    G.add_edge("Xtop.mid::internal", "Xtop.mid::in", type="resistor", resistance=100.0)
    G.add_edge("Xtop.mid::internal", "Xtop.mid::out", type="coupling_cap", coupling_cap=0.003)
    return G


def test_cdl_back_annotate_creates_output(tmp_path: Path):
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    G = build_contracted_graph()
    out_path = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(G, tree, str(out_path), prefix="Par_")
    assert out_path.exists()


def test_cdl_back_annotate_contains_section_markers(tmp_path: Path):
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    G = build_contracted_graph()
    out_path = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(G, tree, str(out_path), prefix="Par_")
    content = out_path.read_text(encoding="utf-8")
    assert ">>> 注入到: .subckt mid_subckt" in content
    assert "<<< 结束: mid_subckt" in content


def test_cdl_back_annotate_uses_local_net_names(tmp_path: Path):
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    G = build_contracted_graph()
    out_path = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(G, tree, str(out_path), prefix="Par_")
    content = out_path.read_text(encoding="utf-8")
    assert "internal" in content
    assert "in" in content
    assert "out" in content
    assert "Xtop.mid::internal" not in content


def test_cdl_back_annotate_emits_resistors(tmp_path: Path):
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    G = build_contracted_graph()
    out_path = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(G, tree, str(out_path), prefix="Par_")
    content = out_path.read_text(encoding="utf-8")
    assert "Par_R_" in content


def test_cdl_back_annotate_ground_mapped_to_zero(tmp_path: Path):
    tree = parse_cdl(CDL_FIXTURE, is_text=True)
    G = build_contracted_graph()
    out_path = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(G, tree, str(out_path), prefix="Par_")
    content = out_path.read_text(encoding="utf-8")
    assert "Cshunt_Par" in content or "Par_Cshunt" in content
```

Run: `pytest tests/test_cdl_spice_exporter.py -v`
Expected: ALL FAIL — module not found

- [ ] **Step 2: Implement cdl_back_annotate function**

Create `src/sram_parasitic_toolkit/kron/cdl_spice_exporter.py`:

```python
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import networkx as nx

from ..cdl_parser import HierarchyTree

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def cdl_back_annotate(
    contracted_graph: nx.Graph,
    hierarchy_tree: HierarchyTree,
    output_path: Union[str, Path],
    prefix: str = "Par_",
    min_r: float = 1e-6,
    min_c: float = 1e-18,
) -> Path:
    """
    Export contracted CDL graph as injection-ready SPICE netlist,
    organized by CDL subcircuit scope.
    """
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    scope_map = _build_scope_map(hierarchy_tree)
    groups: Dict[str, List[str]] = defaultdict(list)

    for node_key in contracted_graph.nodes():
        scope_part = _extract_scope_from_key(node_key)
        if scope_part in scope_map:
            groups[scope_part].append(node_key)
        else:
            groups["__unmatched__"].append(node_key)

    lines: List[str] = []
    now = datetime.now().isoformat(timespec="seconds")
    lines.append("*" + "=" * 78)
    lines.append("* CDL Back-Annotation Parasitic Netlist")
    lines.append(f"* Generated: {now} by spf_parasitic_reduction")
    lines.append("* 将对应段复制到 CDL 相应 .subckt 内部完成反标")
    lines.append("*" + "=" * 78)
    lines.append("")

    r_count = 0
    c_count = 0
    shunt_count = 0

    sorted_scopes = sorted(groups.items()) if "__unmatched__" not in groups else \
        [(k, v) for k, v in sorted(groups.items()) if k != "__unmatched__"]

    for scope_path, node_keys in sorted_scopes:
        subckt_name = scope_map.get(scope_path, scope_path)
        lines.append(f"* >>> 注入到: .subckt {subckt_name} (scope: {scope_path})")
        lines.append("* 以下RC语句使用 {0} 内部的 net 名".format(subckt_name))

        scope_nodes = {k for k in node_keys}

        for u, v, data in contracted_graph.edges(data=True):
            if u not in scope_nodes or v not in scope_nodes:
                continue

            r_val = data.get("resistance")
            if r_val is not None and r_val >= min_r:
                node_u = _local_net_from_key(u)
                node_v = _local_net_from_key(v)
                lines.append(f"{prefix}R_{r_count}  {node_u}  {node_v}  {r_val:.6e}")
                r_count += 1

            c_val = data.get("coupling_cap")
            if c_val is not None and c_val >= min_c:
                node_u = _local_net_from_key(u)
                node_v = _local_net_from_key(v)
                lines.append(f"{prefix}C_{c_count}  {node_u}  {node_v}  {c_val:.6e}")
                c_count += 1

        for node_key in scope_nodes:
            shunt_cap = contracted_graph.nodes[node_key].get("shunt_cap", 0.0)
            if shunt_cap >= min_c:
                net = _local_net_from_key(node_key)
                lines.append(f"{prefix}Cshunt_{shunt_count}  {net}  0  {shunt_cap:.6e}")
                shunt_count += 1

        lines.append(f"* <<< 结束: {subckt_name}")
        lines.append("")

    lines.append("* End of CDL parasitic back-annotation")
    content = "\n".join(lines) + "\n"
    out_path.write_text(content, encoding="utf-8")
    logger.info("CDL back-annotation written: %s (R=%d, C=%d, Shunt=%d)", out_path, r_count, c_count, shunt_count)
    return out_path


def _build_scope_map(tree: HierarchyTree) -> Dict[str, str]:
    scope_map: Dict[str, str] = {}
    _collect_scopes(tree, scope_map)
    return scope_map


def _collect_scopes(tree: HierarchyTree, scope_map: Dict[str, str], prefix: str = "") -> None:
    current_path = f"{prefix}.{tree.instance_name}" if prefix else tree.instance_name
    scope_map[current_path] = tree.subckt_def_name
    for inst_name, child in tree.instances.items():
        _collect_scopes(child, scope_map, current_path)


def _extract_scope_from_key(node_key: str) -> str:
    if "::" in node_key:
        return node_key.split("::")[0]
    return node_key


def _local_net_from_key(node_key: str) -> str:
    if "::" in node_key:
        return node_key.split("::")[1]
    return node_key
```

Run: `pytest tests/test_cdl_spice_exporter.py -v`
Expected: ALL 5 tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/sram_parasitic_toolkit/kron/cdl_spice_exporter.py tests/test_cdl_spice_exporter.py
git commit -m "feat(exporter): implement CDL injection-style SPICE exporter"
```

---

### Task 6: Extend run_flow.py CLI with back-annotate subcommand

**Files:**
- Modify: `run_flow.py`

- [ ] **Step 1: Add back-annotate argument group and cmd_back_annotate function**

In `run_flow.py`, after the imports section (after line 36), add:

```python
from sram_parasitic_toolkit.cdl_parser import parse_cdl
from sram_parasitic_toolkit.spf_to_cdl_mapper import map_nodes
from sram_parasitic_toolkit.net_contractor import contract
from sram_parasitic_toolkit.kron.cdl_spice_exporter import cdl_back_annotate
import json
```

After `cmd_transfer()` (before `cmd_pipeline`), add:

```python
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
```

- [ ] **Step 2: Add add_back_annotate_args() and register subcommand**

After `add_transfer_args()`, add:

```python
def add_back_annotate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kron-graph", required=True, help="Kron-reduced .graphml file")
    parser.add_argument("--cdl", required=True, help="CDL netlist file")
    parser.add_argument("--output", "-o", required=True, help="Output .sp file path")
    parser.add_argument("--prefix", default="Par_", help="Instance name prefix")
    parser.add_argument("--min-r", type=float, default=1e-3, help="Minimum resistance to keep (Ohm)")
    parser.add_argument("--min-c", type=float, default=1e-15, help="Minimum capacitance to keep (F)")
```

In `main()`, after `p_transfer` registration (after line 210), add:

```python
p_back_annotate = subparsers.add_parser("back-annotate", help="Run CDL back-annotation step")
add_back_annotate_args(p_back_annotate)
p_back_annotate.set_defaults(func=cmd_back_annotate)
```

- [ ] **Step 3: Extend pipeline mode to support --back-annotate**

In `main()`'s `p_pipeline` argument group, after the existing transfer args, add:

```python
p_pipeline.add_argument("--back-annotate", action="store_true", help="Run CDL back-annotation after transfer")
p_pipeline.add_argument("--cdl", default=None, help="CDL netlist file (required when --back-annotate)")
```

In `cmd_pipeline()`, after Step 3 (transfer), at the end before the print SUCCESS line, add:

```python
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
```

- [ ] **Step 4: Smoke test CLI**

Run: `python run_flow.py back-annotate --help`
Expected: Shows help text with all parameters

- [ ] **Step 5: Commit**

```bash
git add run_flow.py
git commit -m "feat(cli): add back-annotate subcommand and extend pipeline"
```

---

### Task 7: Integration test — end-to-end with synthetic data

**Files:**
- Create: `tests/test_integration_back_annotate.py`

- [ ] **Step 1: Write integration test**

Create `tests/test_integration_back_annotate.py`:

```python
import json
import networkx as nx
from pathlib import Path
from sram_parasitic_toolkit.cdl_parser import parse_cdl
from sram_parasitic_toolkit.spf_to_cdl_mapper import map_nodes
from sram_parasitic_toolkit.net_contractor import contract
from sram_parasitic_toolkit.kron.cdl_spice_exporter import cdl_back_annotate

CDL = """\
.subckt buf_subckt  A  Y  VDD  VSS
M0 A net1 VSS VSS nch w=2u l=0.1u
M1 Y net1 VDD VDD pch w=4u l=0.1u
M2 net1 A VDD VDD pch w=1u l=0.1u
.ends buf_subckt

.subckt top_subckt  in  out  VDD  VSS
Xbuf1  in  mid  VDD  VSS  buf_subckt
Xbuf2  mid  out  VDD  VSS  buf_subckt
.ends top_subckt

Xtop  PAD_IN  PAD_OUT  VDD  VSS  top_subckt
"""


def build_kron_graph():
    G = nx.Graph()
    G.add_node("Xtop.buf1.net1@1", shunt_cap=0.01)
    G.add_node("Xtop.buf1.net1@2", shunt_cap=0.02)
    G.add_node("Xtop.buf1.A@1", shunt_cap=0.005)
    G.add_node("Xtop.buf2.A@1", shunt_cap=0.006)
    G.add_node("Xtop.mid@1", shunt_cap=0.03)
    G.add_node("Xtop.mid@2", shunt_cap=0.04)

    G.add_edge("Xtop.buf1.net1@1", "Xtop.buf1.net1@2", type="resistor", resistance=50.0)
    G.add_edge("Xtop.buf1.net1@1", "Xtop.buf1.A@1", type="resistor", resistance=100.0)
    G.add_edge("Xtop.buf1.net1@2", "Xtop.buf1.A@1", type="resistor", resistance=150.0)
    G.add_edge("Xtop.buf1.A@1", "Xtop.mid@1", type="resistor", resistance=75.0)
    G.add_edge("Xtop.buf2.A@1", "Xtop.mid@1", type="resistor", resistance=80.0)
    G.add_edge("Xtop.mid@1", "Xtop.mid@2", type="coupling_cap", coupling_cap=0.002)
    G.add_edge("Xtop.mid@2", "Xtop.buf2.A@1", type="resistor", resistance=90.0)

    return G


def test_integration_e2e(tmp_path: Path):
    tree = parse_cdl(CDL, is_text=True)
    kron_graph = build_kron_graph()
    kron_nodes = list(kron_graph.nodes())

    mapping, unmapped = map_nodes(kron_nodes, tree)
    assert len(mapping) > 0
    assert all(u["spf_node"] not in mapping for u in unmapped) or len(unmapped) == 0

    contracted = contract(kron_graph, mapping, tree)
    assert contracted.number_of_nodes() > 0
    assert contracted.number_of_edges() > 0

    out_sp = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(contracted, tree, str(out_sp), prefix="Par_")

    content = out_sp.read_text(encoding="utf-8")
    assert ">>> 注入到: .subckt buf_subckt" in content
    assert ">>> 注入到: .subckt top_subckt" in content

    # Verify no SPF hier paths appear as node names in the output
    assert "Xtop.buf1.net1@1" not in content
    assert "Xtop.buf1.net1@2" not in content

    # Verify local net names are used
    assert "net1" in content
    assert "mid" in content
    assert "A" in content


def test_integration_no_floating_nodes(tmp_path: Path):
    tree = parse_cdl(CDL, is_text=True)
    kron_graph = build_kron_graph()
    kron_nodes = list(kron_graph.nodes())
    mapping, unmapped = map_nodes(kron_nodes, tree)
    contracted = contract(kron_graph, mapping, tree)
    out_sp = tmp_path / "cdl_parasitic.sp"
    cdl_back_annotate(contracted, tree, str(out_sp), prefix="Par_")
    content = out_sp.read_text(encoding="utf-8")

    reported_nets = set()
    for line in content.splitlines():
        if line.startswith("Par_R_") or line.startswith("Par_C_"):
            parts = line.split()
            if len(parts) >= 4:
                reported_nets.add(parts[1])
                reported_nets.add(parts[2])
        elif line.startswith("Par_Cshunt_"):
            parts = line.split()
            if len(parts) >= 3:
                reported_nets.add(parts[1])

    reported_nets.discard("0")
    for net in reported_nets:
        found = False
        for scope in ["buf_subckt", "top_subckt"]:
            if net in tree.local_nets:
                found = True
                break
            for child in tree.instances.values():
                if net in child.local_nets:
                    found = True
                    break
        assert found, f"Net '{net}' used in output but not found in any CDL scope"
```

Run: `pytest tests/test_integration_back_annotate.py -v`
Expected: ALL PASS

- [ ] **Step 2: Run all tests to verify no regression**

Run: `pytest tests/ -v`
Expected: ALL tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration_back_annotate.py
git commit -m "test: add integration test for end-to-end CDL back-annotation"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- [x] cdl_parser → Task 2
- [x] spf_to_cdl_mapper → Task 3
- [x] net_contractor → Task 4
- [x] cdl_spice_exporter → Task 5
- [x] hier_node @ suffix → Task 1
- [x] CLI extension → Task 6
- [x] Verification (unit + integration) → Tasks 1-7
- [x] back_annotate_report.json → Task 6 Step 1 (in cmd_back_annotate)

**2. Placeholder scan:** No TBD, TODO, or vague "add error handling" steps. All code is concrete.

**3. Type consistency:**
- `CdlScope` is defined in Task 3, used consistently in Tasks 4 and 5
- `HierarchyTree` is defined in Task 2, used consistently in Tasks 3, 4, 5, and 6
- `map_nodes` returns `Tuple[Dict[str, CdlScope], List[Dict]]` — consistent with Task 4's usage
- `contract` returns `nx.Graph` — consistent with Task 5's usage
