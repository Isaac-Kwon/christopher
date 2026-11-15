"""Register-file model of the FPGA register bus, driven from cocotb (acts like bombe_array)."""

import cocotb
from cocotb.triggers import FallingEdge


class BusModel:
    """Answers bus_req with bus_ack `delay` cycles later (1..2 per the contract; 0 = never ack)."""

    def __init__(self, dut, delay=1, regs=None):
        self.dut = dut
        self.delay = delay
        self.regs = dict(regs or {})
        self.log = []                    # ("W"|"R", addr, data)
        self.reqs = 0
        dut.bus_ack.value = 0
        dut.bus_rdata.value = 0
        self._task = cocotb.start_soon(self._run())

    def stop(self):
        self._task.cancel()

    async def _run(self):
        dut = self.dut
        while True:
            await FallingEdge(dut.clk)
            dut.bus_ack.value = 0
            if int(dut.bus_req.value):
                self.reqs += 1
                we, addr, wdata = int(dut.bus_we.value), int(dut.bus_addr.value), int(dut.bus_wdata.value)
                if self.delay == 0:
                    continue
                for _ in range(self.delay - 1):
                    await FallingEdge(dut.clk)
                    dut.bus_ack.value = 0
                    assert not int(dut.bus_req.value), "request while a transaction is pending"
                await FallingEdge(dut.clk)
                if we:
                    self.regs[addr] = wdata
                    self.log.append(("W", addr, wdata))
                    dut.bus_rdata.value = 0xFFFFFFFF  # must be ignored for writes
                else:
                    data = self.regs.get(addr, 0xA5000000 | addr)
                    self.log.append(("R", addr, data))
                    dut.bus_rdata.value = data
                dut.bus_ack.value = 1
