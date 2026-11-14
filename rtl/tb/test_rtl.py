"""pytest entry point: builds each RTL top level with the cocotb runner and runs its cocotb module.

SIM=verilator (default) or SIM=icarus.  Build output goes to $CHRISTOPHER_BUILD (default
~/build/christopher-rtl), never into the project tree (Dropbox).
"""

import os
from pathlib import Path

from cocotb_tools.check_results import get_results
from cocotb_tools.runner import get_runner

TB = Path(__file__).resolve().parent
SRC = TB.parent / "src"
SOURCES = [SRC / f for f in ("wirings_pkg.sv", "scrambler.sv", "bombe_core.sv", "bombe_sweep.sv")]
SIM = os.environ.get("SIM", "verilator")
BUILD = Path(os.environ.get("CHRISTOPHER_BUILD", Path.home() / "build" / "christopher-rtl"))
WAVES = bool(os.environ.get("WAVES"))


def run(top: str, module: str):
    runner = get_runner(SIM)
    build_args = ["-Wno-fatal", "-Wno-lint", "-Wno-style"] if SIM == "verilator" else ["-g2012"]
    build_dir = BUILD / SIM / top
    runner.build(sources=SOURCES, hdl_toplevel=top, build_dir=build_dir, build_args=build_args,
                 always=True, timescale=("1ns", "1ps"), waves=WAVES)
    xml = runner.test(hdl_toplevel=top, test_module=module, build_dir=build_dir, test_dir=TB,
                      extra_env={"PYTHONPATH": str(TB)}, waves=WAVES,
                      results_xml=str(build_dir / "results.xml"))
    passed, failed = get_results(xml)
    assert failed == 0, f"{failed} cocotb test(s) failed (see log above)"


def test_scrambler():
    run("scrambler", "cocotb_scrambler")


def test_bombe_core():
    run("bombe_core", "cocotb_core")


def test_bombe_sweep():
    run("bombe_sweep", "cocotb_sweep")
