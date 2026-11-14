"""cocotb tests for rtl/src/scrambler.sv against the pure-Python reference scrambler."""

import random

import cocotb
from cocotb.triggers import Timer

from refmodel import letters, scrambler
from tbcommon import unpack


async def check(dut, refl, greek, l, m, r, cg, cl, cm, cr, rng):
    dut.refl_id.value = refl
    dut.greek_en.value = greek is not None
    dut.greek_id.value = greek if greek is not None else rng.randrange(16)  # must be ignored
    dut.left_id.value, dut.middle_id.value, dut.right_id.value = l, m, r
    dut.core_g.value, dut.core_l.value, dut.core_m.value, dut.core_r.value = cg, cl, cm, cr
    await Timer(1, unit="ns")
    got = unpack(dut.perm.value.to_unsigned(), 5, 26)
    exp = scrambler(refl, greek, l, m, r, cg, cl, cm, cr)
    assert got == exp, (
        f"scrambler mismatch: refl={refl} greek={greek} L={l} M={m} R={r} core g/l/m/r="
        f"{letters([cg, cl, cm, cr])}\n"
        f"  input    {letters(range(26))}\n  expected {letters(exp)}\n  got      {letters([x % 26 for x in got])}"
        f"{'  (value >= 26 present)' if max(got) > 25 else ''}")


@cocotb.test()
async def random_orders(dut):
    """3000 random wheel orders (all reflectors, with/without greek) and core offsets."""
    rng = random.Random(1234)
    for _ in range(3000):
        l, m, r = rng.sample(range(8), 3)
        greek = rng.choice([None, 8, 9])
        await check(dut, rng.randrange(4), greek, l, m, r, *(rng.randrange(26) for _ in range(4)), rng)


@cocotb.test()
async def enigma_i_aaaaa(dut):
    """Known fact: Enigma I, B, I-II-III, AAA: first key presses use right core 1..5 -> BDZGO."""
    out = []
    for r in range(1, 6):
        dut.refl_id.value, dut.greek_en.value = 0, 0
        dut.greek_id.value, dut.left_id.value, dut.middle_id.value, dut.right_id.value = 8, 0, 1, 2
        dut.core_g.value, dut.core_l.value, dut.core_m.value, dut.core_r.value = 0, 0, 0, r
        await Timer(1, unit="ns")
        out.append(unpack(dut.perm.value.to_unsigned(), 5, 26)[0])
    assert letters(out) == "BDZGO", f"got {letters(out)}"


@cocotb.test()
async def every_right_core_and_wheel(dut):
    """Exhaustive over the right rotor id, its 26 cores and every reflector."""
    rng = random.Random(5)
    for refl in range(4):
        for right in range(8):
            for cr in range(26):
                left, middle = [i for i in range(8) if i != right][:2]
                await check(dut, refl, None, left, middle, right, 0, rng.randrange(26), rng.randrange(26), cr, rng)
