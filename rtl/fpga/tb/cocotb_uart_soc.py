"""End-to-end: UART frames -> uart_bridge -> bombe_array (ID/INFO, register read-back, start/abort)."""

import cocotb
from cocotb.triggers import ClockCycles

import cocotb_uart as u


async def rd(dut, rx, addr):
    rx.clear()
    await u.send(dut, u.rframe(addr))
    await u.wait_bytes(dut, rx, 4)
    return int.from_bytes(bytes(rx), "little")


async def wr(dut, rx, addr, val):
    rx.clear()
    await u.send(dut, u.wframe(addr, val))
    await u.wait_bytes(dut, rx, 1)
    assert rx == [0x06]


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def soc(dut):
    # reuse cocotb_uart.start() but the DUT has a real array instead of the Python bus model
    from cocotb.clock import Clock
    from cocotb.triggers import FallingEdge
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    dut.rxd.value = 1
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)
    rx = []
    cocotb.start_soon(u.monitor_tx(dut, rx))

    assert await rd(dut, rx, 0xF000) == 0x424F4D42
    info = await rd(dut, rx, 0xF004)
    assert info & 0xFF == 2 and (info >> 8) & 0xFF == 16, hex(info)
    order = 3 | 1 << 2 | 8 << 4 | 1 << 8 | 3 << 12 | 0 << 16
    for eng in (0, 1):
        base = eng * 0x1000
        await wr(dut, rx, base + 0x008, order + eng)
        await wr(dut, rx, base + 0x00C, 0x00010207 | 5 << 24)
        await wr(dut, rx, base + 0x010, 0b101)
        await wr(dut, rx, base + 0x100 + 4 * 2, 0x00030A05)
    for eng in (0, 1):
        base = eng * 0x1000
        assert await rd(dut, rx, base + 0x008) == order + eng
        assert await rd(dut, rx, base + 0x00C) == 0x00010207 | 5 << 24
        assert await rd(dut, rx, base + 0x010) == 0b101
        assert await rd(dut, rx, base + 0x108) == 0x00030A05
        assert await rd(dut, rx, base + 0x004) & 3 == 0          # idle, not done
    await wr(dut, rx, 0x0000, 1)                                  # START engine 0
    assert await rd(dut, rx, 0x004) & 1 == 1                      # BUSY
    assert int(dut.busy.value) & 1
    await wr(dut, rx, 0x0000, 2)                                  # ABORT
    await ClockCycles(dut.clk, 200)
    assert await rd(dut, rx, 0x004) & 1 == 0
