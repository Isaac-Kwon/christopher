"""Host library tests with fake transports (no hardware).  Run: python -m pytest rtl/fpga/host/tests"""

import struct
import sys
from pathlib import Path

import pytest

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HOST.parents[2] / "python"))      # repo checkout without installed christopher

from bombe_fpga import (  # noqa: E402
    EdgeLimitError, Farm, FpgaBombe, TransportError, UartTransport, config_from, encode_order,
)
from bombe_fpga.check import check, compare  # noqa: E402
from bombe_fpga.fake import FakeArray, decode_order, software_sweep  # noqa: E402

christopher = pytest.importorskip("christopher")
from christopher import Bombe, Enigma, Menu, WheelOrder  # noqa: E402

PLAIN = "ANXOBERKOMMANDODERWEHRMACHTXWETTERVORHERSAGEFUERDIENORDSEEXHEUTEKLARXWINDAUSWESTXSTAERKEDREI"
CRIB = "WETTERVORHERSAGE"
OFFSET = PLAIN.index(CRIB)
ORDERS = ["B IV-I-V", "B I-II-III", "C II-IV-V", "B V-IV-III", "B III-I-II"]


@pytest.fixture(scope="module")
def menu():
    e = Enigma("enigma-i", rotors=("IV", "I", "V"), reflector="B", rings="AKH", positions="QWE",
               plugboard="AQ BZ CR DM EV FW GK HN IU JP")
    return Menu.from_crib(e.encrypt(PLAIN), CRIB, OFFSET).select(12)


def test_order_encoding_roundtrip():
    for text in ("B II-IV-V", "C-THIN BETA II-IV-I", "B-THIN GAMMA VIII-VI-VII", "C I-II-III"):
        o = WheelOrder.parse(text)
        assert decode_order(encode_order(o)) == o
    assert encode_order(WheelOrder.parse("B II-IV-V")) == 0 | 1 << 8 | 3 << 12 | 4 << 16
    assert encode_order(WheelOrder.parse("C-THIN BETA II-IV-I")) == 3 | 1 << 2 | 8 << 4 | 1 << 8 | 3 << 12 | 0 << 16


def test_config_words(menu):
    cfg = config_from(menu, WheelOrder.parse("B II-IV-V"), True, 5)
    assert cfg.options_word & 31 == ord(menu.test_letter) - 65
    assert cfg.options_word >> 8 & 31 == ord(menu.input_wire) - 65
    assert cfg.options_word >> 16 & 3 == 3 and cfg.options_word >> 24 == 5
    assert len(cfg.edge_words) == len(menu.edges) and cfg.edge_en == (1 << len(menu.edges)) - 1
    e0 = menu.edges[0]
    assert cfg.edge_words[0] == (ord(e0.plain) - 65) | (ord(e0.cipher) - 65) << 8 | e0.pos << 16
    assert config_from(menu, WheelOrder.parse("B II-IV-V")).options_word >> 17 & 1 == 0
    with pytest.raises(EdgeLimitError):
        config_from(menu, WheelOrder.parse("B II-IV-V"), max_edges=3)


@pytest.mark.parametrize("n_engines,fifo", [(1, 1), (2, 4), (4, 64)])
def test_farm_matches_software(menu, n_engines, fifo):
    fake = FakeArray(software_sweep(menu), n_engines=n_engines, fifo_depth=fifo, busy_polls=2)
    fpga = FpgaBombe(fake)
    assert (fpga.n_engines, fpga.max_edges, fpga.clock_mhz) == (n_engines, 16, 100)
    farm = Farm(fpga)
    hw = farm.run(menu, ORDERS, timeout=30)
    sw = Bombe("enigma-i").plug_up(menu).run(ORDERS)
    assert sw, "software run should produce stops"
    rep = compare(hw, sw)
    assert rep.ok, str(rep)
    assert [str(s.wheel_order) for s in hw] == [str(s.wheel_order) for s in sw]
    assert farm.positions_tested == 17576 * len(ORDERS)
    assert sum(len(e.log) for e in fake.engines) == len(ORDERS)       # each order configured exactly once
    if n_engines > 1:
        assert all(e.log for e in fake.engines), "work should spread over all engines"


def test_check_function_and_stop_usable(menu):
    fpga = FpgaBombe(FakeArray(software_sweep(menu), n_engines=3))
    rep = check(fpga, menu, ORDERS[:2])
    assert rep.ok and "IDENTICAL" in str(rep)
    assert all(hasattr(s, "check") for s in rep.fpga)          # real christopher Stop objects


def test_compare_detects_difference(menu):
    sw = Bombe("enigma-i").plug_up(menu).run(ORDERS[:1])
    rep = compare(sw[:-1], sw)
    assert not rep.ok and len(rep.only_software) == 1


def test_diagonal_off_and_turnover_flow_through(menu):
    fake = FakeArray(software_sweep(menu))
    FpgaBombe(fake).run(menu, ORDERS[:1], diagonal_board=False, turnover_at=3)
    order, opt, edges = fake.engines[0].log[0]
    assert opt >> 16 & 1 == 0 and opt >> 17 & 1 == 1 and opt >> 24 == 3


def test_bad_id():
    class Bad:
        def read32(self, a):
            return 0

    with pytest.raises(RuntimeError):
        FpgaBombe(Bad())


class FakeSerial:
    """Byte-level fake of the FPGA UART bridge in front of a FakeArray."""

    def __init__(self, array, drop_replies=0):
        self.array, self.rx, self.drop = array, b"", drop_replies
        self.out = b""
        self.writes = []

    def write(self, data):
        self.writes.append(bytes(data))
        if data[:1] == b"W":
            addr, val = struct.unpack("<HI", data[1:7])
            self.array.write32(addr, val)
            reply = b"\x06"
        else:
            assert data[:1] == b"R" and len(data) == 3
            reply = struct.pack("<I", self.array.read32(struct.unpack("<H", data[1:3])[0]))
        if self.drop:
            self.drop -= 1
        else:
            self.out += reply

    def read(self, n):
        d, self.out = self.out[:n], self.out[n:]
        return d

    def reset_input_buffer(self):
        self.out = b""


def test_uart_framing_and_retry(menu, monkeypatch):
    monkeypatch.setattr(UartTransport, "FRAME_TIMEOUT_S", 0)
    ser = FakeSerial(FakeArray(software_sweep(menu), n_engines=2))
    t = UartTransport(ser)
    assert t.read32(0xF000) == 0x424F4D42
    t.write32(0x1008, 0xAABBCCDD)
    assert ser.writes[-1] == b"W" + bytes([0x08, 0x10, 0xDD, 0xCC, 0xBB, 0xAA])
    assert t.read32(0x1008) == 0xAABBCCDD and ser.writes[-1] == b"R\x08\x10"
    ser.drop = 1                                                  # a lost reply is retried
    assert t.read32(0x1008) == 0xAABBCCDD
    ser.drop = 5
    with pytest.raises(TransportError):
        t.read32(0x1008)


def test_stop_reg_read_is_never_retried(menu, monkeypatch):
    monkeypatch.setattr(UartTransport, "FRAME_TIMEOUT_S", 0)
    ser = FakeSerial(FakeArray(software_sweep(menu)), drop_replies=1)
    n0 = len(ser.writes)
    with pytest.raises(TransportError):
        UartTransport(ser).read32(0x0020)                         # STOP_REG pops: one attempt only
    assert len(ser.writes) - n0 == 1


def test_full_run_over_uart_fake(menu, monkeypatch):
    monkeypatch.setattr(UartTransport, "FRAME_TIMEOUT_S", 0)
    ser = FakeSerial(FakeArray(software_sweep(menu), n_engines=2))
    hw = FpgaBombe(UartTransport(ser)).run(menu, ORDERS[:3])
    assert compare(hw, Bombe("enigma-i").plug_up(menu).run(ORDERS[:3])).ok
