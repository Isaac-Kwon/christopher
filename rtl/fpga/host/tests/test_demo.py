"""Exhibit-mode driver functions, the time-driven fake and the visualizer controller (no GUI, no hardware)."""

import sys
from pathlib import Path

import pytest

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HOST.parents[2] / "python"))

from bombe_fpga import FpgaBombe, config_from  # noqa: E402
from bombe_fpga.driver import POSITION_CYCLES, ST_HOLD, pace_for_rate, rate_for_pace, stecker_candidates  # noqa: E402
from bombe_fpga.fake import FakeDemoArray, position_of, synthetic_model  # noqa: E402
from bombe_fpga.visual import DemoController, banner_text, rate_to_slider, slider_to_rate  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(stop_every=100, **kw):
    clock = Clock()
    sweep, snap = synthetic_model(stop_every)
    arr = FakeDemoArray(sweep, snap, clock=clock, **kw)
    fpga = FpgaBombe(arr)
    e = fpga.engines[0]
    e.wr(0x008, 0 | 1 << 8 | 3 << 12 | 4 << 16)     # order B II-IV-V
    e.wr(0x00C, 4 | 0 << 8 | 1 << 16)               # test E, wire A, diagonal
    for i, (a, b, p) in enumerate([(0, 1, 0), (1, 2, 1), (2, 4, 2), (4, 0, 3)]):
        e.wr(0x100, a | b << 8 | p << 16, i)
    e.wr(0x010, 0b1111)
    return arr, fpga, e, clock


def test_pace_rate_conversion():
    assert pace_for_rate(2, 50) == 25_000_000 - POSITION_CYCLES
    assert pace_for_rate(10_000_000, 50) == 0
    assert rate_for_pace(pace_for_rate(100, 50), 50) == pytest.approx(100, rel=1e-6)
    with pytest.raises(ValueError):
        pace_for_rate(0, 50)


def test_stecker_candidates():
    assert stecker_candidates(1 << 7) == ["H"]
    assert stecker_candidates(0x3FFFFFF & ~(1 << 3)) == ["D"]
    assert len(stecker_candidates(0x3FFFFFF & ~0b11)) == 2


def test_slider_mapping():
    assert slider_to_rate(0) == pytest.approx(0.5)
    assert slider_to_rate(100) is None
    assert rate_to_slider(None) == 100
    assert rate_to_slider(slider_to_rate(40)) == pytest.approx(40)


def test_registers_read_back_and_set_rate():
    arr, fpga, e, clock = make()
    assert e.set_rate(2, fpga.clock_mhz) == 25_000_000 - POSITION_CYCLES
    assert e.rd(0x028) == 25_000_000 - POSITION_CYCLES
    e.set_demo(True, True)
    assert e.demo_flags() == (True, True)


def test_pace_in_time_and_single_step():
    arr, fpga, e, clock = make(stop_every=10 ** 9)          # no stops
    e.set_rate(2, 50)
    e.start()
    assert e.rd(0x014) == 1 and e.snapshot().drums == (0, 0, 0, 0)
    clock.t = 0.49
    assert e.rd(0x014) == 1                                    # 2 positions/s: not yet
    clock.t = 0.51
    assert e.rd(0x014) == 2 and e.snapshot().drums == (0, 0, 0, 1)
    clock.t = 5.01
    assert e.rd(0x014) == 11
    e.set_demo(single_step=True)
    assert e.held()
    n = e.rd(0x014)
    clock.t = 60
    assert e.rd(0x014) == n                                    # single step: time does not advance it
    e.step()
    assert e.rd(0x014) == n + 1
    e.step()
    assert e.rd(0x014) == n + 2 and e.drums() == position_of(n + 1, False)


def test_pause_on_stop_holds_and_snapshot_is_consistent():
    arr, fpga, e, clock = make(stop_every=100)
    e.set_pace(0)
    e.set_demo(pause_on_stop=True)
    e.start()
    clock.t = 10.0                                             # plenty of time to reach the first stop
    assert e.status() & ST_HOLD
    snap = e.snapshot()
    stop_idx = 100 - 7
    assert snap.is_stop and snap.drums == position_of(stop_idx, False) and snap.positions == stop_idx + 1
    assert len(snap.cables) == 26 and len(snap.matrix()) == 26 and len(snap.matrix()[0]) == 26
    assert e.test_register() == snap.cables[4]
    assert e.pop_stops()[0][0] == position_of(stop_idx, False)
    e.step()
    assert not e.held() or e.rd(0x014) == stop_idx + 2


def test_edges_readback():
    arr, fpga, e, clock = make()
    assert e.edges() == [(0, 1, 0), (1, 2, 1), (2, 4, 2), (4, 0, 3)]


def test_controller_menu_poll_and_controls():
    arr, fpga, e, clock = make(stop_every=100)
    c = DemoController(fpga, clock=clock)
    m = c.menu()
    assert m.letters == [0, 1, 2, 4] and m.test_letter == 4 and not m.greek and m.total_positions == 26 ** 3
    assert [l[2] for l in m.links] == [1, 2, 3, 4]
    c.rate = None                                              # full speed
    c.start()
    assert e.rd(0x02C) == 2                                    # pause_on_stop only
    clock.t = 10
    f = c.poll()
    assert f.running and f.held and f.snap.is_stop and f.new_stops
    text, is_stop = banner_text(f.snap, m)
    assert is_stop and text.startswith("STOP at")
    c.step()                                                   # switches to single-step mode
    assert e.demo_flags()[0]
    p0 = e.rd(0x014)
    c.step()
    assert e.rd(0x014) == p0 + 1
    c.start()                                                  # back to running
    assert not e.demo_flags()[0]
    c.stop()
    assert not c.poll().running
    assert banner_text(None, m) == ("READY", False)


def test_controller_measured_rate():
    arr, fpga, e, clock = make(stop_every=10 ** 9)
    c = DemoController(fpga, clock=clock)
    c.rate = 10.0
    c.start()
    rates = []
    for k in range(1, 30):
        clock.t = k * 0.5
        rates.append(c.poll().rate)
    assert rates[-1] == pytest.approx(10.0, rel=0.2)


def test_software_model_snapshot_matches_trace():
    christopher = pytest.importorskip("christopher")
    from christopher import Enigma, Menu, WheelOrder
    from bombe_fpga.fake import software_snapshot, software_sweep

    plain = "ANXOBERKOMMANDODERWEHRMACHTXWETTERVORHERSAGEFUERDIENORDSEEXHEUTEKLARXWINDAUSWESTXSTAERKEDREI"
    crib = "WETTERVORHERSAGE"
    ct = Enigma("enigma-i", rotors=("IV", "I", "V"), reflector="B", rings="AKH", positions="QWE",
                plugboard="AQ BZ CR DM EV FW GK HN IU JP").encrypt(plain)
    menu = Menu.from_crib(ct, crib, plain.index(crib)).select(12)
    cfg = config_from(menu, WheelOrder.parse("B IV-I-V"))
    clock = Clock()
    arr = FakeDemoArray(software_sweep(menu), software_snapshot(menu), clock=clock)
    fpga = FpgaBombe(arr)
    e = fpga.engines[0]
    e.configure(cfg)
    e.set_pace(0)
    e.set_demo(pause_on_stop=True)
    e.start()
    clock.t = 5
    assert e.held()
    snap = e.snapshot()
    assert snap.is_stop
    stops = [s for s in christopher.Bombe("enigma-i").plug_up(menu).run("B IV-I-V") if s._pos == snap.drums]
    assert stops and snap.cables[ord(menu.test_letter) - 65] == stops[0].test_register
