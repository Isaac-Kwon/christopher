"""axil_bridge tests: single and overlapped transactions, channel ordering, backpressure."""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, with_timeout

from busmodel import BusModel


async def start(dut, delay=1, regs=None):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    for s in ("awvalid", "wvalid", "arvalid"):
        getattr(dut, "s_axi_" + s).value = 0
    dut.s_axi_bready.value = 1
    dut.s_axi_rready.value = 1
    dut.s_axi_wstrb.value = 0xF
    bus = BusModel(dut, delay, regs)
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)
    return bus


async def until(dut, ready):
    """Valid was driven at a falling edge.  `ready` only changes on rising edges, so its value now is
    what the DUT sees at the next rising edge; the transfer happens when it is high."""
    while not int(ready.value):
        await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)          # transfer done at the rising edge in between


async def aw(dut, addr, wait=0):
    for _ in range(wait):
        await FallingEdge(dut.clk)
    dut.s_axi_awaddr.value = addr
    dut.s_axi_awvalid.value = 1
    await until(dut, dut.s_axi_awready)
    dut.s_axi_awvalid.value = 0


async def wr(dut, data, wait=0):
    for _ in range(wait):
        await FallingEdge(dut.clk)
    dut.s_axi_wdata.value = data
    dut.s_axi_wvalid.value = 1
    await until(dut, dut.s_axi_wready)
    dut.s_axi_wvalid.value = 0


async def bresp(dut, bready_delay=0):
    while True:
        await FallingEdge(dut.clk)
        if int(dut.s_axi_bvalid.value):
            break
    dut.s_axi_bready.value = 0          # backpressure: bvalid must hold while bready is low
    for _ in range(bready_delay):
        await FallingEdge(dut.clk)
        assert int(dut.s_axi_bvalid.value) == 1
    dut.s_axi_bready.value = 1
    resp = int(dut.s_axi_bresp.value)
    await FallingEdge(dut.clk)
    assert int(dut.s_axi_bvalid.value) == 0
    return resp


async def write(dut, addr, data, aw_wait=0, w_wait=0, bready_delay=0):
    a = cocotb.start_soon(aw(dut, addr, aw_wait))
    w = cocotb.start_soon(wr(dut, data, w_wait))
    await a
    await w
    return await bresp(dut, bready_delay)


async def read(dut, addr, rready_delay=0):
    dut.s_axi_araddr.value = addr
    dut.s_axi_arvalid.value = 1
    await until(dut, dut.s_axi_arready)
    dut.s_axi_arvalid.value = 0
    dut.s_axi_rready.value = 0
    while True:
        await FallingEdge(dut.clk)
        if int(dut.s_axi_rvalid.value):
            break
    data = int(dut.s_axi_rdata.value)
    for _ in range(rready_delay):
        await FallingEdge(dut.clk)
        assert int(dut.s_axi_rvalid.value) == 1 and int(dut.s_axi_rdata.value) == data
    dut.s_axi_rready.value = 1
    resp = int(dut.s_axi_rresp.value)
    await FallingEdge(dut.clk)
    assert int(dut.s_axi_rvalid.value) == 0
    return data, resp


@cocotb.test(timeout_time=200, timeout_unit="us")
async def basic(dut):
    bus = await start(dut, regs={0x1004: 0x600DF00D})
    assert await write(dut, 0x0008, 0x12345678) == 0
    assert bus.log == [("W", 0x8, 0x12345678)]
    assert await read(dut, 0x1004) == (0x600DF00D, 0)
    assert await read(dut, 0x0008) == (0x12345678, 0)
    assert (await read(dut, 0xF001))[1] == 0            # unaligned read: low bits dropped
    assert bus.log[-1][:2] == ("R", 0xF000)


@cocotb.test(timeout_time=200, timeout_unit="us")
async def channel_ordering_and_delays(dut):
    bus = await start(dut)
    for delay in (1, 2, 4):
        bus.delay = delay
        for aw_wait, w_wait in ((0, 0), (0, 5), (5, 0), (3, 3)):
            bus.log.clear()
            assert await write(dut, 0x20, 0xABCD0000 + delay, aw_wait, w_wait) == 0
            assert bus.log == [("W", 0x20, 0xABCD0000 + delay)]
            assert (await read(dut, 0x20))[0] == 0xABCD0000 + delay


@cocotb.test(timeout_time=200, timeout_unit="us")
async def backpressure(dut):
    bus = await start(dut)
    assert await write(dut, 0x40, 0x55, bready_delay=7) == 0
    assert await read(dut, 0x40, rready_delay=9) == (0x55, 0)
    # a read issued while the write response is stalled must wait (one transaction at a time)
    dut.s_axi_bready.value = 0
    a = cocotb.start_soon(aw(dut, 0x44))
    w = cocotb.start_soon(wr(dut, 0x66))
    await a
    await w
    while not int(dut.s_axi_bvalid.value):
        await FallingEdge(dut.clk)
    dut.s_axi_araddr.value = 0x40
    dut.s_axi_arvalid.value = 1
    for _ in range(10):
        await FallingEdge(dut.clk)
        assert int(dut.s_axi_bvalid.value) == 1 and not int(dut.s_axi_arready.value)
    dut.s_axi_bready.value = 1
    await until(dut, dut.s_axi_arready)
    dut.s_axi_arvalid.value = 0
    while not int(dut.s_axi_rvalid.value):
        await FallingEdge(dut.clk)
    assert int(dut.s_axi_rdata.value) == 0x55


@cocotb.test(timeout_time=200, timeout_unit="us")
async def random_traffic(dut):
    bus = await start(dut)
    rng = random.Random(7)
    model = {}
    for _ in range(60):
        bus.delay = rng.choice((1, 2))
        a = rng.randrange(0, 0x10000, 4)
        if rng.random() < 0.5:
            d = rng.getrandbits(32)
            assert await write(dut, a, d, rng.randrange(3), rng.randrange(3), rng.randrange(3)) == 0
            model[a] = d
        else:
            exp = model.get(a, 0xA5000000 | a)
            assert await read(dut, a, rng.randrange(3)) == (exp, 0)


@cocotb.test(timeout_time=200, timeout_unit="us")
async def slverr_on_timeout(dut):
    await start(dut, delay=0)
    assert await with_timeout(read(dut, 0x10), 100, "us") == (0, 2)
    assert await write(dut, 0x10, 1) == 2
