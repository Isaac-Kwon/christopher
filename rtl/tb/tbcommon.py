"""Shared cocotb helpers: clock/reset, packing of port values, driving a menu configuration.

All driving and sampling happens on the falling clock edge, so there are no races with the DUT's
rising-edge logic.  Packed-array ports are flat integers: element i occupies bits [i*W +: W].
"""

from __future__ import annotations

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from refmodel import letters

MAX_EDGES = 16


def pack(values, width: int) -> int:
    return sum(int(v) << (i * width) for i, v in enumerate(values))


def unpack(value: int, width: int, n: int) -> list[int]:
    return [(value >> (i * width)) & ((1 << width) - 1) for i in range(n)]


async def reset(dut, cycles: int = 3):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    dut.start.value = 0
    for _ in range(cycles):
        await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)


def drive_config(dut, cfg: dict, rng: random.Random):
    """Wheel order, menu and options from `refmodel.config_of`. Unused edge slots and the greek id
    of a three-rotor order get random junk, which the RTL must ignore."""
    dut.refl_id.value = cfg["refl_id"]
    dut.greek_en.value = cfg["greek_id"] is not None
    dut.greek_id.value = cfg["greek_id"] if cfg["greek_id"] is not None else rng.randrange(16)
    dut.left_id.value = cfg["left_id"]
    dut.middle_id.value = cfg["middle_id"]
    dut.right_id.value = cfg["right_id"]
    edges = cfg["edges"]
    pad = MAX_EDGES - len(edges)

    def junk(bound):
        return [rng.randrange(bound) for _ in range(pad)]

    dut.edge_en.value = (1 << len(edges)) - 1
    dut.edge_a.value = pack([e[0] for e in edges] + junk(26), 5)
    dut.edge_b.value = pack([e[1] for e in edges] + junk(26), 5)
    dut.edge_pos.value = pack([e[2] for e in edges] + junk(256), 8)
    dut.test_letter.value = cfg["test_letter"]
    dut.input_wire.value = cfg["input_wire"]
    dut.diagonal_en.value = cfg["diagonal"]
    dut.turnover_en.value = cfg["turnover_at"] is not None
    dut.turnover_at.value = cfg["turnover_at"] if cfg["turnover_at"] is not None else rng.randrange(256)


def describe_cfg(cfg: dict) -> str:
    edges = " ".join(f"{letters([a])}{letters([b])}@{p}" for a, b, p in cfg["edges"])
    return (f"order refl={cfg['refl_id']} greek={cfg['greek_id']} L={cfg['left_id']} M={cfg['middle_id']} "
            f"R={cfg['right_id']}, test {letters([cfg['test_letter']])} wire {letters([cfg['input_wire']])}, "
            f"diagonal={cfg['diagonal']} turnover_at={cfg['turnover_at']}, edges [{edges}]")
