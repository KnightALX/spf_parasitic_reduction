
# CDL Back-Annotation Design

> 方案A: SPF→CDL 节点名映射 + 层次化子电路注入
>
> Date: 2026-06-29
> Status: Design Phase
> Target version: v0.4.0

## 1. Problem Statement

### 1.1 Background

当前 `spf_parasitic_reduction` 工具链完成 SPF→RC Graph→Kron Reduction→SPICE Export 的完整流程后，
生成的 `.sp` 文件使用 SPF 层次化节点名（如 `Xtop.xmmio.net147@1`）。
将该文件 `.include` 到 CDL testbench 中时，这些节点名在 CDL 的顶层作用域中不存在，
导致反标后的 RC 全部变成浮空（floating）节点，实际上没有挂载到任何目标端口上。

### 1.2 Root Cause Analysis

问题的根本原因有三个，相互叠加：

**根因1：SPF 与 CDL 的命名体系不兼容（`@` 后缀）**

SPF 使用 `@` 后缀区分同一逻辑 net 上不同物理位置的分支点（如 `net147@1`、`net147@2`）。
但 HSPICE/SPECTRE 不将 `@` 视为标准节点名字符，且 CDL 中不存在这种物理分支区分 — 在 CDL 中只有一条逻辑 net `net147`。

**根因2：SPF 层次引用 vs CDL 作用域隔离**

SPF 通过完整层次路径引用任意子电路内部节点（如 `Xtop.xmmio.net147`）。
但 CDL 的 `.subckt` 内部节点对顶层是私有的 — `.include` 文件在顶层作用域被解析，
HSPICE 看到 `Xtop.xmmio.net147` 时不会自动进入 `Xtop`→`xmmio` 实例的作用域去解析 `net147`。

**根因3：Kron 约简后保留的 transistor-grain 节点无法反标**

Kron 约简的 `non_ground_device` boundary 策略保留了连接晶体管的精细节点（如 `M1:G`、`M2:G`）。
这些节点在 CDL 的 subckt 内部是隐式的（net 连到晶体管端子的信息在 CDL 中已经通过实例化表达），
无法以节点名形式被反标文件引用。

```
SPF 物理视角:                    CDL 逻辑视角:
                                 
Xtop──xmmio──net147@1──┬──M1:G   Xtop──Xxmmio──net147 (一条逻辑net)
                       ├──M2:G        (M1:G、M2:G 是隐式连接，不可直接引用)
Xtop──xmmio──net147@2──M3:S

Kron约简后保留:                   CDL能看到的:
  Xtop.xmmio.net147@1  ←浮空!      net147 (在mmio_subckt作用域内)
  Xtop.xmmio.net147@2  ←浮空!
```

### 1.3 Scope

本设计仅解决 **Kron 约简后的 RC 图反标回 CDL** 阶段的问题。
不涉及 SPF 解析、RC 图构建、Kron 约简算法本身的修改。

## 2. Design Overview

### 2.1 Core Idea

不依赖 SPICE 仿真器的层次引用语法。
取而代之的是：
1. 解析 CDL 网表，构建子电路层次树（HierarchyTree）
2. 建立 SPF 节点名 → CDL 作用域内节点名的映射表
3. 将 Kron 约简图中映射到同一 CDL net 的节点合并
4. 按 CDL 子电路作用域分组输出 RC 语句，直接注入到 CDL 内部

### 2.2 Pipeline Extension

```
                  ┌──────────┐
                  │  SPF文件  │
                  └────┬─────┘
                       │
                ┌──────▼──────┐
                │    Refine    │  → rc_graph.graphml + manifest.json
                │  (不变)      │
                └──────┬──────┘
                       │
                ┌──────▼──────┐
                │    Reduce    │  → kron_reduced.graphml
                │  (不变)      │
                └──────┬──────┘
                       │
         ┌─────────────┤
         │             │
  ┌──────▼──────┐     │
  │   CDL文件   │     │
  └──────┬──────┘     │
         │             │
  ┌──────▼─────────────▼──────┐
  │     Back-Annotate (新增)   │
  │                           │
  │  cdl_parser          → HierarchyTree
  │  spf_to_cdl_mapper   → Mapping table
  │  net_contractor      → Contracted Graph
  │  cdl_spice_exporter  → cdl_parasitic.sp
  └──────────────┬────────────┘
                 │
                 ▼
         cdl_parasitic.sp   ← 可直接注入CDL子电路内部
```

### 2.3 Output Mode: Injection

生成的文件 `cdl_parasitic.sp` 按 CDL 子电路作用域分段组织。
用户将对应段的 R/C 语句复制到 CDL 相应 `.subckt` 内部即可完成反标。

### 2.4 New / Modified Files

| 文件 | 类型 | 职责 |
|------|------|------|
| `src/sram_parasitic_toolkit/cdl_parser.py` | 新增 | 解析 CDL，构建 HierarchyTree |
| `src/sram_parasitic_toolkit/spf_to_cdl_mapper.py` | 新增 | SPF 节点名→CDL 作用域映射 |
| `src/sram_parasitic_toolkit/net_contractor.py` | 新增 | 节点收缩合并 + 端口识别 |
| `src/sram_parasitic_toolkit/kron/cdl_spice_exporter.py` | 新增 | 按 subckt 注入式 SPICE 输出 |
| `src/sram_parasitic_toolkit/hier_node.py` | 修改 | 新增 `@` 后缀解析 |
| `run_flow.py` | 修改 | 新增 `back-annotate` 子命令 + 扩展 pipeline |

## 3. Module Design

### 3.1 cdl_parser.py — CDL 层次解析器

**职责**: 解析 HSPICE CDL 格式网表，构建子电路层次树。

**输入**: CDL 文件路径

**输出**: HierarchyTree — 以顶层 `Xtop` 实例为根的树结构

**核心数据结构**:

```
HierarchyTree
├── scope_name: str          # "top_subckt"
├── instance_name: str       # "Xtop" (在父级中的实例名)
├── subckt_def_name: str     # "top_subckt"
├── ports: List[str]         # ["data_in", "addr", "VDD", "VSS"]
├── local_nets: Set[str]     # {所有内部net名}
├── instances: Dict[str, HierarchyTree]   # {"xmmio" → 子树, ...}
└── parent: Optional[HierarchyTree]
```

**解析算法**:

```
1. 扫描所有 .subckt ... .ends 块 → 建立 subckt_def 注册表
   - 记录每个 subckt 的名称、端口列表
2. 在每个 .subckt 内部:
   a. 收集所有节点名 → local_nets
   b. 识别 X 开头的行 → 子电路实例
      Xinst_name  port1 port2 ...  subckt_type_name
      → 创建子 HierarchyTree 节点
3. 找到顶层实例化: 不在任何.subckt内的 X 行 → 根节点
4. 递归展开: 每个实例的 subckt_type_name → 查注册表 → 填充内部结构
```

**HSPICE CDL 语法要点**:

| 语法元素 | 识别规则 | 备注 |
|---------|---------|------|
| `.subckt NAME port1 port2 ...` | 行首 `.subckt` | 子电路定义开始 |
| `.ends [NAME]` | 行首 `.ends` | 子电路定义结束 |
| `Xinst_name port1 port2 ... TYPE` | 行首 `X`（不跟在 `*` `$` 后） | 实例化语句 |
| `*` 开头行 | 注释 | 跳过 |
| `$` 开头行 | 注释 | 跳过 |
| `+` 开头的续行 | 拼接前一行 | 行续接 |
| 带 `parameter=value` 的行 | 含 `=` | 参数赋值行 |

### 3.2 spf_to_cdl_mapper.py — 节点名映射器

**职责**: 将 SPF 层次化节点名映射到 CDL HierarchyTree 中的对应作用域和 local net 名。

**输入**: 
- Kron 约简图的节点列表（SPF 层次化节点名）
- HierarchyTree 根节点

**输出**: 
- `MappingResult` dict: `{spf_node → CdlScope(scope_path, local_net)}`
- `unmapped_nodes` list: 无法映射的节点及原因

**CdlScope 数据结构**:

```
CdlScope
├── scope_path: str        # "Xtop.xmmio" (实例路径)
├── local_net: str         # "net147" (CDL内部net名)
├── is_port: bool          # 该net是否为subckt端口
├── spf_branch: str        # "1" (保留@后缀信息)
└── hierarchy_node: HierarchyTree  # 目标作用域节点引用
```

**映射算法**（以 `Xtop.xmmio.net147@1` 为例）:

```
Step 1: 剥离 @ 后缀
  "Xtop.xmmio.net147@1" → base="Xtop.xmmio.net147", branch="1"

Step 2: 按 . 分割路径
  path_parts = ["Xtop", "xmmio", "net147"]

Step 3: 遍历 HierarchyTree 匹配实例路径
  root.instance_name = "Xtop"  ✓
  → root.instances["xmmio"]  ✓
  → 此时已到达 mmio_subckt 作用域

Step 4: 验证 net 在目标作用域中存在
  mmio_subckt.local_nets 含 "net147" ✓ → 映射成功

Step 5: 返回 MappingResult
```

**无法映射的处理**: 路径中任一实例名在 HierarchyTree 中找不到，或 net 名在目标 subckt 的 local_nets 中不存在 → 标记为 unmapped，记录 warning 和原因。

### 3.3 net_contractor.py — 节点收缩合并器

**职责**: 将 Kron 约简图中映射到同一 CDL net 的多个 SPF 节点合并为一个，
处理 @ 分支间的 R/C 边，为对外边做并行等效合并。

**输入**:
- Kron-reduced Graph (networkx)
- MappingResult dict

**输出**: ContractedGraph — 节点以 `"scope_path::local_net"` 为 key

**合并策略**:

| 类型 | 策略 | 理由 |
|------|------|------|
| 组内 R 边 | 短路合并（丢弃） | 同一条逻辑 net 上的分布式 R，合并后无意义 |
| 组内 C 边 | 加到节点 shunt_cap | 同一条 net 的寄生电容直接累加 |
| 组内 shunt_cap | 求和 | 物理上的并行电容 |
| 组间 R 边（并行） | 1/R_eq = Σ(1/R_i) | 多个 @ 分支到同一个外部 net 的 R |
| 组间 C 边（并行） | C_eq = Σ(C_i) | 多个 @ 分支到同一个外部 net 的 C |
| 跨 subckt 边 | 端口识别 + 回退丢弃 | 见 3.4 节 |

**跨 subckt 边处理**:

当 Kron 约简图中存在连接不同 subckt 内部 net 的边时：

```
策略c（端口识别）—— 优先:
  if net_A 恰好是 subckt_A 的端口:
    → 在上层 subckt 中，该端口对应一个连接 net
    → 使用上层 net 名输出 R/C 边
  if net_B 恰好是 subckt_B 的端口:
    → 同理

策略b（回退丢弃）—— 兜底:
  if 两侧都不是端口:
    → 丢弃此边，记录 warning + report
```

**节点收缩伪代码**:

```python
def contract(kron_graph, mapping, r_merge_threshold=1e6):
    # 1. 构建合并组: group_key = "scope_path::local_net"
    merge_groups = {}
    for spf_node, scope in mapping.items():
        key = f"{scope.scope_path}::{scope.local_net}"
        merge_groups.setdefault(key, []).append(spf_node)

    # 2. 创建合并后节点，sum shunt_cap
    result = nx.Graph()
    for group_key, spf_nodes in merge_groups.items():
        total_shunt = sum(kron_graph.nodes[n].get("shunt_cap", 0.0) for n in spf_nodes)
        result.add_node(group_key, shunt_cap=total_shunt, num_merged=len(spf_nodes))

    # 3. 处理所有边
    for u, v, data in kron_graph.edges(data=True):
        g_u = find_group(u); g_v = find_group(v)
        if g_u is None or g_v is None: continue  # orphan
        
        if g_u == g_v:
            # 组内边 → shunt
            c = data.get("coupling_cap") or data.get("coupling_cap_approx", 0.0)
            result.nodes[g_u]["shunt_cap"] += c
        else:
            # 组间边 → 并行合并
            merge_parallel_edge(result, g_u, g_v, data)
    
    return finalize(result)
```

### 3.4 cdl_spice_exporter.py — CDL 注入式 SPICE 导出器

**职责**: 将 ContractedGraph 按 CDL 子电路作用域分组，生成可直接注入的 SPICE 网表。

**输出格式**:

```spice
* ================================================================
* CDL Back-Annotation Parasitic Netlist
* Generated: 2026-06-29 by spf_parasitic_reduction v0.4.0
* 
* 每个注释块标记了目标注入的 subckt 作用域
* 将对应段复制到 CDL 相应 .subckt 内部完成反标
* ================================================================

* >>> 注入到: .subckt mmio_subckt (scope: Xtop.xmmio)
* 以下RC语句使用 mmio_subckt 内部的 net 名
R_par_0  net147  net148  27.27
C_par_0  net147  net149  0.05e-15
Cshunt_par_0  net147  0  0.12e-15
* <<< 结束: mmio_subckt

* >>> 注入到: .subckt dec_subckt (scope: Xtop.xdec)
R_par_10  net55  net56  150.0
Cshunt_par_1  net55  0  0.08e-15
* <<< 结束: dec_subckt

* >>> 注入到: .subckt top_subckt (scope: Xtop)
* 顶层RC（如果存在跨subckt的RC边，已通过端口识别映射到对应端口）
R_par_20  data_in  addr  500.0
* <<< 结束: top_subckt

* End of CDL parasitic back-annotation
```

**关键约定**:
- 节点名使用 CDL 内部的 local net 名（不是 SPF 层次路径）
- VSS/GND 统一映射为 `0`
- 器件名使用 `R_par_` / `C_par_` / `Cshunt_par_` 前缀，避免与 CDL 原有器件名冲突
- 每个注入段有明确的 `>>>` / `<<<` 注释标记

### 3.5 hier_node.py — 扩展 @ 后缀解析

在现有 `HierNode` 数据类和 `parse_hier_node()` 函数中新增 `@` 后缀处理：

```python
@dataclass
class HierNode:
    raw: str
    base_net: str
    hierarchy: List[str]
    port: Optional[str]
    branch: Optional[str] = None  # ← 新增: @ 后的分支号
    is_ground: bool = False

# parse_hier_node() 中新增:
# "Xtop.xmmio.net147@1" → 先按 @ 分割
#   branch = "1"
#   剩余 "Xtop.xmmio.net147" → 按现有逻辑解析
```

## 4. CLI Extension

### 4.1 New Subcommand: `back-annotate`

```bash
python run_flow.py back-annotate \
    --kron-graph   results/kron_reduced.graphml \
    --cdl          design.cdl \
    --output       results/cdl_parasitic.sp \
    --prefix       Par_ \
    --min-r        1e-3 \
    --min-c        1e-15
```

**参数**:

| Flag | 说明 | 默认值 |
|------|------|--------|
| `--kron-graph` | Kron 约简后的 GraphML（必填） | — |
| `--cdl` | CDL 网表文件（必填） | — |
| `--output` | 输出反标 .sp 文件路径 | cdl_parasitic.sp |
| `--prefix` | 器件名前缀 | Par_ |
| `--min-r` | 最小电阻阈值 (Ω) | 1e-3 |
| `--min-c` | 最小电容阈值 (F) | 1e-15 |
| `--subckt-name` | 限制只反标到指定 subckt | 无（全部） |
| `--preserve-orphans` | 保留无法映射节点为顶层浮空 | False |

### 4.2 Extended Pipeline

```bash
python run_flow.py pipeline \
    --spf design.spf \
    --cdl design.cdl \              # ← 新增
    --outdir ./results \
    --graph --tc-aware --sram-role \
    --hierarchical --s 0 \
    --back-annotate \                # ← 新增 flag
    --prefix Par_ --min-r 1e-3 --min-c 1e-15
```

### 4.3 Output Files

```
results/
├── manifest.json               # refine 统计
├── rc_graph.graphml            # 全量 RC 图
├── kron_reduced.graphml        # Kron 约简图
├── cdl_parasitic.sp            # ★ CDL 反标文件
└── back_annotate_report.json   # 映射质量报告 (新增)
```

### 4.4 back_annotate_report.json

```json
{
  "spf_file": "design.spf",
  "cdl_file": "design.cdl",
  "total_spf_nodes_in_kron_graph": 1247,
  "mapped_nodes": 1180,
  "unmapped_nodes": 67,
  "num_merge_groups": 203,
  "num_cdl_scopes": 5,
  "merge_groups": [
    {
      "scope_path": "Xtop.xmmio",
      "local_net": "net147",
      "merged_spf_nodes": ["Xtop.xmmio.net147@1", "Xtop.xmmio.net147@2"],
      "total_shunt_cap_f": 0.00012
    }
  ],
  "unmapped_details": [
    {
      "spf_node": "Xtop.xunknown.ghost_net",
      "reason": "instance 'xunknown' not found in CDL hierarchy"
    }
  ],
  "cross_scope_edges": [
    {
      "from_scope": "Xtop.xmmio", "from_net": "net147",
      "to_scope": "Xtop.xdec",   "to_net": "net55",
      "type": "resistor", "resistance_ohm": 200.0,
      "resolution": "discarded (no connecting port found)"
    }
  ]
}
```

## 5. Design Decisions Summary

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 输出模式 | 注入式（直接可复制到 CDL subckt 内部） | 用户选择模式2 |
| @ 分支处理 | 映射到同一 CDL local_net | @ 是物理分支标识，CDL 中是一条逻辑 net |
| 组内 R 边 | 短路合并（丢弃） | 同一 net 内部分布式 R 精度损失可接受 |
| 跨 subckt 边 | 端口识别 + 回退丢弃 | 用户确认 |
| ground 映射 | 统一为 `0` | SPICE 标准地节点 |
| 器件名前缀 | `Par_` (可配置) | 避免与 CDL 原有器件名冲突 |
| 无法映射节点 | 丢弃 + warning + report | 保证输出完整性 |

## 6. Verification Strategy

### 6.1 Unit Tests (模块级)

- `cdl_parser`: 对示例 CDL 片段正确构建 HierarchyTree
- `spf_to_cdl_mapper`: 正确映射和识别无法映射的节点
- `net_contractor`: 同组内节点正确合并，并行 RC 正确等效
- `cdl_spice_exporter`: 输出格式正确，节点名在目标 subckt 中存在

### 6.2 Integration Test (端到端)

- 用小型 `example.spf` + `example.cdl` 跑完整 pipeline
- 检查输出 `.sp` 中所有节点名在 CDL 对应 subckt 中存在
- 解析输出 `.sp` 确认没有浮空节点

### 6.3 HSPICE Simulation (真实环境)

- 将生成的 `cdl_parasitic.sp` 注入实际 CDL 子电路
- 运行仿真，检查是否有 `floating node` warning
- 对比"不加寄生"vs"加寄生"仿真结果，确认 RC 产生预期影响
