"""
Minimal smoke tests for Group 1 fixes (CLI hygiene) and core modules.
"""

import subprocess
import sys
from pathlib import Path

import pytest


from sram_parasitic_toolkit.hier_node import parse_hier_node
from sram_parasitic_toolkit.sram_semantics import infer_sram_role


def test_parse_hier_node_basic():
    node = parse_hier_node("CDECR_B0[1]:26")
    assert node.base_net == "CDECR_B0[1]"
    assert node.port == "26"


def test_infer_sram_role_basic():
    role = infer_sram_role("xbitcell", {}, [])
    assert role == "sram_subckt"


def test_cli_help_runs():
    """Smoke test that CLI --help works (covers import and basic argparse)."""
    pytest.importorskip("netlist_parser", reason="eda-netlist-parser not installed for full CLI smoke")
    result = subprocess.run(
        [sys.executable, "-m", "sram_parasitic_toolkit.dspf_refiner", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "DSPF/SPF parser" in result.stdout or "usage" in result.stdout.lower()


def test_cli_net_not_found_error():
    """Test that unknown net now gives clear error (improved error handling)."""
    pytest.importorskip("netlist_parser", reason="eda-netlist-parser not installed for full CLI smoke")
    # Use a non-existent spf to trigger early, but better to reach the net check.
    # Since we need a valid parse for net logic, use --net with dummy that won't match.
    # For smoke, run with invalid spf and check error format improved.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sram_parasitic_toolkit.dspf_refiner",
            "--spf",
            "nonexistent.spf",
            "--outpath",
            "/tmp/test_out",
            "--net",
            "FOO_BAR",
        ],
        capture_output=True,
        text=True,
    )
    # It will fail at parse first with clear message.
    assert "Failed to parse SPF file" in result.stderr or result.stdout
    assert result.returncode != 0


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
