"""HAL for the Raspberry Pi Pico (RP2040) / Pico 2 (RP2350), MicroPython. NOT YET RUN ON HARDWARE.

Pin map (README section 4.5):
  74HC595 chain (7 chips, 56 bits -> 7 x TBD62783A high-side drivers): SPI0 SCK GP18, MOSI GP19,
      latch (RCLK) GP17, output enable (OE, active low, external pull-up) GP16
  74HC165 chain (10 chips, 80 bits): SPI1 SCK GP10, MISO GP12, load (SH/LD, active low) GP13
  STROBE input (own optocoupler)  GP2 ; RUN output GP3 ; HOME output GP4
  ADC option: mux select GP20..GP23 (S0..S3), mux enable (active low) GP6..GP9, analog in GP26 (ADC0)

Bit order of the 165 chain (first bit shifted in = highest): see SENSE_BITS below.
"""

import machine
import time
import rp2  # noqa: F401  (kept for a later PIO implementation of the sweep)

from tester_core import NODES

# 165 chain bit assignment: bits 0..51 sense nodes 0..51; 52..66 POS[0..14]; then flags.
POS_SHIFT = 52
FLAG_SHIFT = 67          # STOPPED, AT_HOME, END, ESTOP_OK, ILOCK_OK, JUDGE, CLEAR at consecutive bits (67..73)
SENSE_BITS = 80


class PicoHal:
    name = "pico"
    has_adc = True

    def __init__(self):
        self.settle_us = 100
        self.oe = machine.Pin(16, machine.Pin.OUT, value=1)       # drivers disabled
        self.rclk = machine.Pin(17, machine.Pin.OUT, value=0)
        self.out_spi = machine.SPI(0, baudrate=8_000_000, polarity=0, phase=0,
                                   sck=machine.Pin(18), mosi=machine.Pin(19), miso=None)
        self.ld = machine.Pin(13, machine.Pin.OUT, value=1)
        self.in_spi = machine.SPI(1, baudrate=4_000_000, polarity=0, phase=0,
                                  sck=machine.Pin(10), mosi=None, miso=machine.Pin(12))
        self.strobe = machine.Pin(2, machine.Pin.IN)
        self.run_pin = machine.Pin(3, machine.Pin.OUT, value=0)
        self.home_pin = machine.Pin(4, machine.Pin.OUT, value=0)
        self.sel = [machine.Pin(p, machine.Pin.OUT, value=0) for p in (20, 21, 22, 27)]  # GP23 is not on the Pico header (board: MUX_S3 = GP27)
        self.en = [machine.Pin(p, machine.Pin.OUT, value=1) for p in (6, 7, 8, 9)]
        self.adc_in = machine.ADC(26)
        self._rise = 0
        self._fall = 0
        self._edge_flag = False
        self.strobe.irq(trigger=machine.Pin.IRQ_RISING | machine.Pin.IRQ_FALLING, handler=self._irq)
        self.drive(0)
        self.oe.value(0)

    # -- drive ------------------------------------------------------------------------------
    def drive(self, mask):
        buf = mask.to_bytes(7, "big")        # 56 bits, last chip first
        self.out_spi.write(buf)
        self.rclk.value(1)
        self.rclk.value(0)

    def settle(self):
        time.sleep_us(self.settle_us)

    # -- sense --------------------------------------------------------------------------------
    def _read_chain(self):
        self.ld.value(0)
        self.ld.value(1)
        raw = self.in_spi.read(SENSE_BITS // 8)
        v = 0
        for b in raw:                         # first byte = last chip in chain = highest bits
            v = v << 8 | b
        return v

    def sense(self):
        return self._read_chain() & ((1 << NODES) - 1)

    def aux(self):
        v = self._read_chain()
        pos = v >> POS_SHIFT & 0x7FFF
        flags = (v >> FLAG_SHIFT & 0x7F) << 1 | (self.strobe.value() & 1)
        # flags bit order: STROBE, STOPPED, AT_HOME, END, ESTOP_OK, ILOCK_OK, JUDGE, CLEAR
        return pos, flags & 0xFF

    def set_run(self, v):
        self.run_pin.value(v)

    def set_home(self, v):
        self.home_pin.value(v)

    # -- STROBE ---------------------------------------------------------------------------------
    def _irq(self, pin):
        t = time.ticks_us()
        if pin.value():
            self._rise = t
            self._edge_flag = True
        else:
            self._fall = t

    def wait_strobe(self, timeout_ms):
        self._edge_flag = False
        self._fall = 0
        t0 = time.ticks_ms()
        while not self._edge_flag:
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                return False
        return True

    def strobe_width(self):
        t0 = time.ticks_ms()
        while not self._fall:
            if time.ticks_diff(time.ticks_ms(), t0) > 50:
                return -1
        return time.ticks_diff(self._fall, self._rise)

    # -- time / ADC ----------------------------------------------------------------------------
    def ticks_us(self):
        return time.ticks_us()

    def ticks_since(self, t0):
        return time.ticks_diff(time.ticks_us(), t0)

    def adc(self, nd):
        for e in self.en:
            e.value(1)
        chip, ch = divmod(nd, 16)
        for k, p in enumerate(self.sel):
            p.value(ch >> k & 1)
        self.en[chip].value(0)
        time.sleep_us(50)
        total = 0
        for _ in range(16):
            total += self.adc_in.read_u16() >> 4
        self.en[chip].value(1)
        return total >> 4
