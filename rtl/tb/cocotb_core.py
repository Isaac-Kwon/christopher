"""cocotb tests for rtl/src/bombe_core.sv against the Rust-core vectors in data/vectors/rtl/."""

import random
import time

import cocotb
from cocotb.triggers import FallingEdge

from refmodel import FULL, config_of, letters, load_jsonl, mask_letters
from tbcommon import describe_cfg, drive_config, reset

FILES = [
    "enigma-i_B-III-V-II.jsonl",                       # default options (no diagonal/turnover keys)
    "enigma-i_turnover5_B-I-II-III.jsonl",
    "enigma-i_nodiag_B-II-IV-V.jsonl",
    "enigma-i_weak_C-III-V-II.jsonl",
    "m4_B-THIN_BETA_II-IV-I.jsonl",
    "m4_C-THIN_GAMMA_VI-VIII-VII_turnover9.jsonl",
]
TIMEOUT = 3000  # cycles


async def run_one(dut, early_exit: bool):
    """Pulse start, wait for done; return (stop, test_register, live, iterations)."""
    dut.early_exit.value = early_exit
    dut.start.value = 1
    await FallingEdge(dut.clk)
    dut.start.value = 0
    for _ in range(TIMEOUT):
        if dut.done.value == 1:
            assert dut.busy.value == 0, "busy still high when done pulses"
            return (int(dut.stop.value), dut.test_register.value.to_unsigned(),
                    dut.live.value.to_unsigned(), int(dut.iterations.value))
        await FallingEdge(dut.clk)
    raise AssertionError(f"no done within {TIMEOUT} cycles")


def live_diff(exp: list[int], got: int) -> str:
    rows = []
    for c in range(26):
        g = (got >> (26 * c)) & FULL
        if g != exp[c]:
            rows.append(f"  cable {letters([c])}: expected live {mask_letters(exp[c])}  got {mask_letters(g)}")
    return "\n".join(rows[:8]) + (f"\n  ... {len(rows)} cables differ" if len(rows) > 8 else "")


@cocotb.parametrize(vfile=FILES)
async def vectors(dut, vfile):
    """Every vector: early_exit=0 must reproduce `live`, `stop`, `test_register` exactly; with
    early_exit=1 only `stop` and `test_register` must agree."""
    t0 = time.time()
    rng = random.Random(99)
    await reset(dut)
    rows = load_jsonl(vfile)
    max_iter = 0
    for i, v in enumerate(rows):
        cfg = config_of(v)
        drive_config(dut, cfg, rng)
        g, l, m, r = v["position"]
        dut.pos_g.value, dut.pos_l.value, dut.pos_m.value, dut.pos_r.value = g, l, m, r
        where = (f"{vfile} #{i} {v['wheel_order']} drums g/l/m/r={letters([g, l, m, r])}\n  "
                 f"{describe_cfg(cfg)}")
        exp_reg = v["live"][v["test_letter"]]
        for early in (False, True):
            stop, reg, live, iters = await run_one(dut, early)
            assert bool(stop) == v["stop"], f"{where}\n  early_exit={early}: stop expected {v['stop']} got {bool(stop)}"
            assert reg == exp_reg, (f"{where}\n  early_exit={early}: test register expected "
                                    f"{mask_letters(exp_reg)} got {mask_letters(reg)}")
            if not early:
                exp_live = sum(x << (26 * c) for c, x in enumerate(v["live"]))
                assert live == exp_live, f"{where}\n  early_exit=0: live state differs\n{live_diff(v['live'], live)}"
                max_iter = max(max_iter, iters)
    dut._log.info(f"{vfile}: {len(rows)} vectors x 2 modes ok, max iterations {max_iter}, {time.time() - t0:.1f}s")
