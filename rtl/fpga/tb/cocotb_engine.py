"""cocotb tests for rtl/fpga/src/bombe_array.sv (+ bombe_engine.sv) through the register bus.

Each engine runs a full sweep and its stop list is compared, in order, with the Rust core's
(`Bombe.run`) reference in data/vectors/rtl/sweep_*.json — the same references used for the
simulation-only rtl/src/bombe_sweep.sv.
"""

from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tb"))
from refmodel import config_of, letters, load_json, mask_letters  # noqa: E402

CTRL, STATUS, ORDER, OPTIONS, EDGE_EN, POSITIONS, CYCLES, STOP_POS, STOP_REG, PARAMS = (
    0x000, 0x004, 0x008, 0x00C, 0x010, 0x014, 0x018, 0x01C, 0x020, 0x024)
EDGE0 = 0x100
N_ENGINES = int(os.environ.get("N_ENGINES", "2"))


class Bus:
    """Register bus master; drives on the falling edge, one request per cycle."""

    def __init__(self, dut):
        self.dut = dut

    async def _req(self, we: int, addr: int, data: int = 0) -> int:
        d = self.dut
        d.bus_req.value, d.bus_we.value, d.bus_addr.value, d.bus_wdata.value = 1, we, addr, data
        await FallingEdge(d.clk)
        d.bus_req.value = 0
        for _ in range(4):
            if int(d.bus_ack.value):
                return d.bus_rdata.value.to_unsigned()
            await FallingEdge(d.clk)
        raise AssertionError(f"no bus_ack for {'write' if we else 'read'} 0x{addr:04x}")

    async def write(self, addr: int, data: int):
        await self._req(1, addr, data)

    async def read(self, addr: int) -> int:
        return await self._req(0, addr)


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    dut.bus_req.value = 0
    for _ in range(3):
        await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)


async def configure(bus: Bus, base: int, cfg: dict):
    greek = cfg["greek_id"]
    order = (cfg["refl_id"] | (int(greek is not None) << 2) | ((greek or 0) << 4)
             | (cfg["left_id"] << 8) | (cfg["middle_id"] << 12) | (cfg["right_id"] << 16))
    t = cfg["turnover_at"]
    options = (cfg["test_letter"] | (cfg["input_wire"] << 8) | (int(cfg["diagonal"]) << 16)
               | (int(t is not None) << 17) | ((t or 0) << 24))
    await bus.write(base + ORDER, order)
    await bus.write(base + OPTIONS, options)
    for i, (a, b, pos) in enumerate(cfg["edges"]):
        await bus.write(base + EDGE0 + 4 * i, a | (b << 8) | (pos << 16))
    await bus.write(base + EDGE_EN, (1 << len(cfg["edges"])) - 1)


async def drain(bus: Bus, base: int) -> list[dict]:
    out = []
    while (await bus.read(base + STATUS)) & 0b100:
        pos = await bus.read(base + STOP_POS)
        reg = await bus.read(base + STOP_REG)
        assert pos >> 31, "STOP_POS not valid although STOP_AVAILABLE"
        out.append({"pos": [(pos >> s) & 31 for s in (20, 15, 10, 5)], "register": reg})
    return out


def fmt(stop) -> str:
    return f"drums {letters(stop['pos'])} test register {mask_letters(stop['register'])}"


def compare(name, got, exp):
    for i, (g, e) in enumerate(zip(got, exp)):
        assert g == e, f"{name}: stop #{i} differs\n  expected {fmt(e)}\n  got      {fmt(g)}"
    assert len(got) == len(exp), f"{name}: {len(got)} stops, expected {len(exp)}"


async def run_engines(dut, refs: list[dict], max_stops=None, poll=97):
    """Start one reference per engine, drain all FIFOs while running. Returns per-engine
    (stops, positions, cycles)."""
    bus = Bus(dut)
    bases = [k << 12 for k in range(len(refs))]
    for base, ref in zip(bases, refs):
        await configure(bus, base, config_of(ref))
    for base in bases:
        await bus.write(base + CTRL, 1)
    stops = [[] for _ in refs]
    running = set(range(len(refs)))
    while running:
        for _ in range(poll):
            await FallingEdge(dut.clk)
        for k in list(running):
            stops[k] += await drain(bus, bases[k])
            if max_stops is not None and len(stops[k]) >= max_stops:
                await bus.write(bases[k] + CTRL, 2)  # abort
                running.discard(k)
            elif not (await bus.read(bases[k] + STATUS)) & 1:
                stops[k] += await drain(bus, bases[k])
                running.discard(k)
    result = []
    for base, s in zip(bases, stops):
        result.append((s, await bus.read(base + POSITIONS), await bus.read(base + CYCLES)))
    return result


@cocotb.test()
async def global_block(dut):
    await reset(dut)
    bus = Bus(dut)
    assert await bus.read(0xF000) == 0x424F4D42
    info = await bus.read(0xF004)
    assert info & 0xFF == N_ENGINES and (info >> 8) & 0xFF == 16, hex(info)
    params = await bus.read(PARAMS)
    assert params & 0xFF == 16, hex(params)
    # Edge registers read back.
    await bus.write(EDGE0 + 4 * 3, 0x0A_05_07)
    assert await bus.read(EDGE0 + 4 * 3) == 0x0A_05_07


ENIGMA_PAIRS = [
    ("sweep_enigma-i_B-III-V-II_11edges.json", "sweep_enigma-i_C-V-IV-I_13edges_nodiag_turnover5.json"),
    ("sweep_enigma-i_B-III-V-II_14edges.json", "sweep_enigma-i_B-III-V-II_11edges.json"),
]


@cocotb.parametrize(pair=ENIGMA_PAIRS)
async def enigma_sweeps(dut, pair):
    """Two engines, two full Enigma I sweeps at once: ordered stop lists and position counts."""
    await reset(dut)
    refs = [load_json(n) for n in pair[:N_ENGINES]]
    t0 = time.time()
    results = await run_engines(dut, refs)
    for name, ref, (stops, positions, cycles) in zip(pair, refs, results):
        compare(name, stops, ref["stops"])
        assert positions == ref["positions_tested"], f"{name}: positions {positions}"
        dut._log.info(f"{name}: {len(stops)} stops, {cycles} cycles ({cycles / positions:.1f}/position)")
    dut._log.info(f"wall {time.time() - t0:.1f}s")


@cocotb.test()
async def m4_prefix(dut):
    """M4 (greek wheel, turnover-free menu): first 60 stops in order, then ABORT."""
    await reset(dut)
    ref = load_json("sweep_m4_B-THIN_BETA_II-IV-I_11edges.json")
    full = os.environ.get("M4_FULL") == "1"
    [(stops, positions, cycles)] = await run_engines(dut, [ref], max_stops=None if full else 60)
    if full:
        compare("m4 full", stops, ref["stops"])
        assert positions == ref["positions_tested"]
    else:
        compare("m4 prefix", stops[:60], ref["stops"][:60])
    dut._log.info(f"m4: {len(stops)} stops, {positions} positions, {cycles} cycles")


@cocotb.test()
async def restart_discards_old_stops(dut):
    """A second START after an ABORT starts clean and reproduces the same first stops."""
    await reset(dut)
    ref = load_json("sweep_enigma-i_B-III-V-II_11edges.json")
    [(first, _, _)] = await run_engines(dut, [ref], max_stops=5)
    [(second, _, _)] = await run_engines(dut, [ref], max_stops=5)
    compare("restart", second[:5], ref["stops"][:5])
    compare("first run", first[:5], ref["stops"][:5])
    rnd = random.Random(1)
    assert rnd  # keep import used for future randomised configs
