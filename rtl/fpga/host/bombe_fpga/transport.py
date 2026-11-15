"""Register-bus transports.  A transport only needs read32(addr) and write32(addr, value)."""

from __future__ import annotations

import struct
import time


class TransportError(IOError):
    pass


class Transport:
    """Abstract 32-bit register bus (byte addresses, word aligned)."""

    def read32(self, addr: int) -> int:
        raise NotImplementedError

    def write32(self, addr: int, value: int) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class UartTransport(Transport):
    """UART protocol of the Nexys Video bridge (little-endian, lock-step).

    ``port`` is a pyserial port name (e.g. ``COM5``, ``/dev/ttyUSB1``) or an already opened object
    with ``write``/``read``/``reset_input_buffer`` (tests inject one).  The bridge drops bytes that
    arrive while it is answering, so requests are never pipelined.
    """

    FRAME_TIMEOUT_S = 0.015   # FPGA abandons a partial frame after ~10 ms of silence

    def __init__(self, port, baud: int = 921_600, timeout: float = 0.5, retries: int = 2):
        if isinstance(port, str):
            import serial  # pyserial, imported lazily so the module works without it

            port = serial.Serial(port, baud, timeout=timeout)
        self.ser = port
        self.retries = retries

    def resync(self) -> None:
        """Drop any partial frame in the FPGA (silence > its timeout) and flush stale replies."""
        time.sleep(self.FRAME_TIMEOUT_S)
        self.ser.reset_input_buffer()

    def _exchange(self, frame: bytes, n_reply: int, retries: int | None = None) -> bytes:
        last = None
        for _ in range((self.retries if retries is None else retries) + 1):
            self.ser.write(frame)
            reply = self.ser.read(n_reply)
            if len(reply) == n_reply:
                return reply
            last = reply
            self.resync()
        raise TransportError(f"no/short reply ({len(last)} of {n_reply} bytes) for frame {frame.hex()}")

    def read32(self, addr: int) -> int:
        # reads are idempotent except STOP_REG (offset 0x020 of an engine, which pops the FIFO):
        # that one is never retried, a lost reply is reported as an error instead of losing a stop.
        pop = addr < 0xF000 and (addr & 0xFFF) == 0x020
        reply = self._exchange(b"R" + struct.pack("<H", addr), 4, 0 if pop else None)
        return struct.unpack("<I", reply)[0]

    def write32(self, addr: int, value: int) -> None:
        reply = self._exchange(b"W" + struct.pack("<HI", addr, value & 0xFFFFFFFF), 1)
        if reply != b"\x06":
            raise TransportError(f"bad write ack {reply.hex()}")

    def close(self) -> None:
        close = getattr(self.ser, "close", None)
        if close:
            close()


class MmioTransport(Transport):
    """PYNQ: wraps ``pynq.MMIO``.  ``base`` is the bridge's AXI base address (see the overlay's
    ``.hwh``/``ip_dict``); the register window is 64 KiB."""

    def __init__(self, base: int | None = None, length: int = 0x10000, mmio=None):
        if mmio is None:
            from pynq import MMIO  # only exists on the board

            mmio = MMIO(base, length)
        self.mmio = mmio

    def read32(self, addr: int) -> int:
        return int(self.mmio.read(addr)) & 0xFFFFFFFF

    def write32(self, addr: int, value: int) -> None:
        self.mmio.write(addr, value & 0xFFFFFFFF)
