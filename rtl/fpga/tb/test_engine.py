"""pytest entry for the FPGA engine: builds bombe_array (N_ENGINES engines) and runs cocotb_engine.

Run from WSL:  rtl/tb/run.sh ../fpga/tb/test_engine.py   (or rtl/fpga/tb/run_engine.sh)
"""

import os
from pathlib import Path

from cocotb_tools.check_results import get_results
from cocotb_tools.runner import get_runner

TB = Path(__file__).resolve().parent
ROOT = TB.parents[2]
SOURCES = [
    ROOT / "rtl" / "src" / "wirings_pkg.sv",
    ROOT / "rtl" / "fpga" / "src" / "bombe_engine.sv",
    ROOT / "rtl" / "fpga" / "src" / "bombe_array.sv",
]
SIM = os.environ.get("SIM", "verilator")
BUILD = Path(os.environ.get("CHRISTOPHER_BUILD", Path.home() / "build" / "christopher-rtl"))
N_ENGINES = int(os.environ.get("N_ENGINES", "2"))


def test_bombe_array():
    runner = get_runner(SIM)
    build_args = ["-Wno-fatal", "-Wno-lint", "-Wno-style"] if SIM == "verilator" else ["-g2012"]
    build_dir = BUILD / SIM / "bombe_array"
    runner.build(sources=SOURCES, hdl_toplevel="bombe_array", build_dir=build_dir, build_args=build_args,
                 parameters={"N_ENGINES": N_ENGINES}, always=True, timescale=("1ns", "1ps"))
    xml = runner.test(hdl_toplevel="bombe_array", test_module="cocotb_engine", build_dir=build_dir,
                      test_dir=TB, extra_env={"PYTHONPATH": str(TB), "N_ENGINES": str(N_ENGINES)},
                      results_xml=str(build_dir / "results.xml"))
    _, failed = get_results(xml)
    assert failed == 0, f"{failed} cocotb test(s) failed (see log above)"
