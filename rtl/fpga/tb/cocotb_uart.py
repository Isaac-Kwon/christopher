"""uart_bridge tests.  Built with CLK_HZ/BAUD chosen so one bit = DIV clocks (fast simulation)."""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from busmodel import BusModel

DIV = int(os.environ.get("UART_DIV", "16"))
TIMEOUT = int(os.environ.get("UART_TIMEOUT", "2000"))


async def start(dut, delay=1, regs=None):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    dut.rxd.value = 1
    bus = BusModel(dut, delay, regs)
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)
    rx = []
    cocotb.start_soon(monitor_tx(dut, rx))
    return bus, rx


async def send_byte(dut, b, stop_ok=True, div=DIV):
    for bit in [0] + [(b >> i) & 1 for i in range(8)] + [1 if stop_ok else 0]:
        dut.rxd.value = bit
        await ClockCycles(dut.clk, div)
    dut.rxd.value = 1


async def send(dut, data, gap=0):
    for b in data:
        await send_byte(dut, b)
        if gap:
            await ClockCycles(dut.clk, gap)


async def monitor_tx(dut, out):
    while True:
        await FallingEdge(dut.clk)
        if int(dut.txd.value) == 0:
            await ClockCycles(dut.clk, DIV // 2)
            assert int(dut.txd.value) == 0, "start bit glitch"
            v = 0
            for i in range(8):
                await ClockCycles(dut.clk, DIV)
                v |= int(dut.txd.value) << i
            await ClockCycles(dut.clk, DIV)
            assert int(dut.txd.value) == 1, "bad stop bit on txd"
            out.append(v)


def wframe(addr, data):
    return bytes([0x57, addr & 255, addr >> 8]) + data.to_bytes(4, "little")


def rframe(addr):
    return bytes([0x52, addr & 255, addr >> 8])


async def wait_bytes(dut, rx, n, limit=40 * DIV * 8):
    for _ in range(limit):
        if len(rx) >= n:
            return
        await ClockCycles(dut.clk, 1)
    raise AssertionError(f"expected {n} response bytes, got {rx}")


async def idle(dut, cycles=None):
    await ClockCycles(dut.clk, cycles or 30 * DIV)


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def write_and_read(dut):
    bus, rx = await start(dut, delay=1, regs={0x0008: 0x11223344})
    await send(dut, wframe(0x0010, 0xDEADBEEF))
    await wait_bytes(dut, rx, 1)
    assert rx == [0x06]
    assert bus.log == [("W", 0x10, 0xDEADBEEF)]
    rx.clear()
    await send(dut, rframe(0x0008))
    await wait_bytes(dut, rx, 4)
    assert bytes(rx) == (0x11223344).to_bytes(4, "little")
    rx.clear()
    await send(dut, rframe(0xF004))               # full 16-bit address
    await wait_bytes(dut, rx, 4)
    assert bus.log[-1][:2] == ("R", 0xF004)
    assert bytes(rx) == (0xA500F004).to_bytes(4, "little")


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def ack_delays_and_back_to_back(dut):
    bus, rx = await start(dut, delay=1)
    for delay in (1, 2, 5):
        bus.delay = delay
        rng = random.Random(delay)
        for i in range(8):
            a, d = rng.randrange(0, 0x10000, 4), rng.getrandbits(32)
            rx.clear()
            await send(dut, wframe(a, d))
            await wait_bytes(dut, rx, 1)
            assert rx == [0x06]
            rx.clear()
            await send(dut, rframe(a))           # immediately after the previous answer
            await wait_bytes(dut, rx, 4)
            assert int.from_bytes(bytes(rx), "little") == d, f"delay {delay} addr {a:#x}"
        await idle(dut)


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def garbage_ignored_and_framing(dut):
    bus, rx = await start(dut)
    await send(dut, bytes([0x00, 0xFF, 0x41, 0x06, 0x00]))   # noise while idle
    await idle(dut, 5 * DIV)
    assert not rx and bus.reqs == 0
    await send_byte(dut, 0x57, stop_ok=False)                # framing error: dropped
    await idle(dut, 5 * DIV)
    assert not rx and bus.reqs == 0
    await send(dut, rframe(0x20))
    await wait_bytes(dut, rx, 4)
    assert bus.log == [("R", 0x20, 0xA5000020)]


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def partial_frame_timeout_resync(dut):
    bus, rx = await start(dut, regs={0x4: 0x01020304})
    for partial in (wframe(0x10, 1)[:3], wframe(0x10, 1)[:6], rframe(0x4)[:2], b"\x57"):
        await send(dut, partial)
        await ClockCycles(dut.clk, TIMEOUT + 4 * DIV)         # frame abandoned
        assert bus.reqs == 0 and not rx
        await send(dut, rframe(0x4))
        await wait_bytes(dut, rx, 4)
        assert bytes(rx) == bytes([4, 3, 2, 1]), partial
        rx.clear()
        bus.reqs = 0
        bus.log.clear()
    # slow sender: inter-byte gaps shorter than the timeout must NOT abort the frame
    await send(dut, wframe(0x30, 0xCAFEF00D), gap=TIMEOUT // 2)
    await wait_bytes(dut, rx, 1)
    assert bus.log == [("W", 0x30, 0xCAFEF00D)]


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def host_resync_with_zero_flush(dut):
    """README: host may send 8 x 0x00 then retry."""
    bus, rx = await start(dut)
    await send(dut, b"\x57\x10")                 # host died mid-frame
    await send(dut, bytes(8))                    # flush: completes (harmless) frame then ignored zeros
    await wait_bytes(dut, rx, 1)
    assert rx == [0x06]
    assert bus.log == [("W", 0x10, 0)]           # 6 of the 8 zeros completed the frame, 2 ignored
    await idle(dut, 5 * DIV)


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def bus_never_acks(dut):
    bus, rx = await start(dut, delay=0)
    await send(dut, rframe(0x44))
    await wait_bytes(dut, rx, 4, limit=2000 * DIV)
    assert bytes(rx) == bytes(4)
    rx.clear()
    bus.stop()
    bus2 = BusModel(dut, 1, {0x44: 7})           # still alive afterwards
    await send(dut, rframe(0x44))
    await wait_bytes(dut, rx, 4)
    assert bytes(rx) == bytes([7, 0, 0, 0])
