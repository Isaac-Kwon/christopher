"""cocotb tests for rtl/src/bombe_sweep.sv against Bombe.run stop lists (data/vectors/rtl/sweep_*.json)."""

import os
import random
import time

import cocotb
from cocotb.triggers import FallingEdge

from refmodel import config_of, letters, load_json, mask_letters
from tbcommon import describe_cfg, drive_config, reset

# (reference file, probability that stop_ready is high in a cycle; 1.0 = always ready)
CASES = [
    ("sweep_enigma-i_B-III-V-II_14edges.json", 1.0),
    ("sweep_enigma-i_B-III-V-II_11edges.json", 1.0),
    ("sweep_enigma-i_B-III-V-II_11edges.json", 0.3),
    ("sweep_enigma-i_C-V-IV-I_13edges_nodiag_turnover5.json", 0.5),
]
M4_REF = "sweep_m4_B-THIN_BETA_II-IV-I_11edges.json"
M4_PREFIX = 40  # stops compared on the M4 case; M4_FULL=1 runs all 456,976 positions


def fmt(stop) -> str:
    return f"drums {letters(stop['pos'])} test register {mask_letters(stop['register'])}"


async def sweep(dut, ref: dict, p_ready: float, max_stops, seed: int):
    """Run the sweep, accepting stops with probability p_ready per cycle.
    Returns (stops, done_seen, positions_tested, cycles)."""
    rng = random.Random(seed)
    await reset(dut)
    drive_config(dut, config_of(ref), rng)
    dut.stop_ready.value = 0
    dut.start.value = 1
    await FallingEdge(dut.clk)
    dut.start.value = 0
    stops, pending, cycles, limit = [], None, 0, 400 * ref["positions_tested"]
    while cycles < limit:
        valid = int(dut.stop_valid.value)
        cur = None
        if valid:
            cur = {"pos": [int(getattr(dut, f"stop_{n}").value) for n in "glmr"],
                   "register": dut.stop_register.value.to_unsigned()}
        if pending is not None:  # backpressure: a stalled stop must be held unchanged
            assert cur == pending, f"stop changed while stalled: {fmt(pending)} -> {cur and fmt(cur)}"
        if int(dut.done.value):
            assert not valid, "stop_valid high together with done"
            return stops, True, dut.positions_tested.value.to_unsigned(), cycles
        ready = rng.random() < p_ready
        dut.stop_ready.value = ready  # seen by the DUT at the next rising edge, with `valid` as sampled now
        pending = cur if (valid and not ready) else None
        if valid and ready:
            stops.append(cur)
            if max_stops is not None and len(stops) >= max_stops:
                return stops, False, None, cycles
        await FallingEdge(dut.clk)
        cycles += 1
    return stops, False, None, cycles


def compare(name, got, exp):
    for i, (g, e) in enumerate(zip(got, exp)):
        assert g == e, f"{name}: stop #{i} differs\n  expected {fmt(e)}\n  got      {fmt(g)}"
    if len(got) != len(exp):
        longer = got if len(got) > len(exp) else exp
        raise AssertionError(f"{name}: {len(got)} stops, expected {len(exp)}; first "
                             f"{'extra' if len(got) > len(exp) else 'missing'}: {fmt(longer[min(len(got), len(exp))])}")


@cocotb.parametrize(case=CASES)
async def enigma_sweep(dut, case):
    """Full 17,576-position Enigma I sweep: ordered stop list, registers and positions_tested."""
    name, p_ready = case
    ref = load_json(name)
    t0 = time.time()
    stops, done, tested, cycles = await sweep(dut, ref, p_ready, None, seed=7)
    assert done, f"{name}: no done after {cycles} cycles ({len(stops)} stops so far)"
    compare(f"{name} (p_ready={p_ready}) [{describe_cfg(config_of(ref))}]", stops, ref["stops"])
    assert tested == ref["positions_tested"], f"positions_tested {tested}, expected {ref['positions_tested']}"
    dut._log.info(f"{name} p_ready={p_ready}: {len(stops)} stops, {cycles} cycles "
                  f"({cycles / tested:.1f}/position), {time.time() - t0:.1f}s")


@cocotb.test()
async def m4_prefix(dut):
    """M4 (greek wheel): the first stops must match in order (greek 0 slice). M4_FULL=1: whole sweep."""
    ref = load_json(M4_REF)
    full = os.environ.get("M4_FULL") == "1"
    t0 = time.time()
    stops, done, tested, cycles = await sweep(dut, ref, 0.5, None if full else M4_PREFIX, seed=11)
    if full:
        assert done, "M4 sweep did not finish"
        compare("m4 full", stops, ref["stops"])
        assert tested == ref["positions_tested"], f"positions_tested {tested}, expected {ref['positions_tested']}"
    else:
        compare("m4 prefix", stops, ref["stops"][:M4_PREFIX])
    dut._log.info(f"m4 {'full' if full else 'prefix'}: {len(stops)} stops, {cycles} cycles, {time.time() - t0:.1f}s")
