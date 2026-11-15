"""pytest entry: builds each bridge with the cocotb runner (same conventions as rtl/tb/test_rtl.py).

Run from WSL via rtl/fpga/tb/run.sh.  Build output goes to $CHRISTOPHER_BUILD, never into the project.
"""

import os
from pathlib import Path

from cocotb_tools.check_results import get_results
from cocotb_tools.runner import get_runner

TB = Path(__file__).resolve().parent
SRC = TB.parent / "src"
SIM = os.environ.get("SIM", "verilator")
BUILD = Path(os.environ.get("CHRISTOPHER_BUILD", Path.home() / "build" / "christopher-rtl")) / "fpga"
WAVES = bool(os.environ.get("WAVES"))


def run(top, module, sources, parameters=None, extra_env=None):
    runner = get_runner(SIM)
    build_args = ["-Wno-fatal", "-Wno-lint", "-Wno-style"] if SIM == "verilator" else ["-g2012"]
    build_dir = BUILD / SIM / f"{top}_{(extra_env or {}).get('UART_DIV', '')}"
    runner.build(sources=[SRC / s for s in sources], hdl_toplevel=top, build_dir=build_dir,
                 build_args=build_args, parameters=parameters or {}, always=True,
                 timescale=("1ns", "1ps"), waves=WAVES)
    env = {"PYTHONPATH": str(TB), **(extra_env or {})}
    xml = runner.test(hdl_toplevel=top, test_module=module, build_dir=build_dir, test_dir=TB,
                      extra_env=env, waves=WAVES, results_xml=str(build_dir / "results.xml"))
    passed, failed = get_results(xml)
    assert failed == 0, f"{failed} cocotb test(s) failed (see log above)"


def test_uart_bridge():
    # 16 clocks per bit, 2000-clock frame timeout: keeps simulation fast
    run("uart_bridge", "cocotb_uart", ["uart_bridge.sv"],
        parameters={"CLK_HZ": 16 * 115200, "BAUD": 115200, "TIMEOUT_CYCLES": 2000, "ACK_TIMEOUT": 64},
        extra_env={"UART_DIV": "16", "UART_TIMEOUT": "2000"})


def test_uart_bridge_odd_divider():
    # non-integer clocks per bit (rounded divider): 17 clocks/bit here
    run("uart_bridge", "cocotb_uart", ["uart_bridge.sv"],
        parameters={"CLK_HZ": 17 * 115200, "BAUD": 115200, "TIMEOUT_CYCLES": 2000, "ACK_TIMEOUT": 64},
        extra_env={"UART_DIV": "17", "UART_TIMEOUT": "2000"})


def test_axil_bridge():
    run("axil_bridge", "cocotb_axil", ["axil_bridge.sv"], parameters={"ACK_TIMEOUT": 64})


def test_uart_to_array():
    """UART frames end-to-end into the real bombe_array (ID/INFO, register read-back, start/abort)."""
    run("uart_soc", "cocotb_uart_soc", ["../../src/wirings_pkg.sv", "../tb/uart_soc.sv", "uart_bridge.sv",
                                        "bombe_engine.sv", "bombe_array.sv"],
        extra_env={"UART_DIV": "16", "UART_TIMEOUT": "2000"})
