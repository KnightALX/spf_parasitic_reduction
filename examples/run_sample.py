#!/usr/bin/env python3
"""
Example: How to use the SRAM Parasitic Toolkit programmatically.
"""

from pathlib import Path
import sys

# Add src to path for direct execution (src layout)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sram_parasitic_toolkit.rc_graph import RCGraphBuilder
from netlist_parser import NetlistParser

if __name__ == "__main__":
    print("=== SRAM Parasitic Toolkit Example ===")
    print("For CLI usage see: python -m sram_parasitic_toolkit.dspf_refiner --help")
    print("\nProgrammatic usage example (requires a real .spf file):")
    print("""
from netlist_parser import NetlistParser
from sram_parasitic_toolkit.rc_graph import RCGraphBuilder

netlist = NetlistParser().parse("your_design.spf")
rcg = RCGraphBuilder(netlist)

print(rcg.get_summary_stats())
print(rcg.compute_net2net_totals("YOUR_NET_NAME"))
rcg.to_graphml("rc_graph.graphml")
""")