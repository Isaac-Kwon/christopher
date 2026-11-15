"""Tests of the scrambler unit tester (hardware/common/unit-tester): host, firmware logic, simulation."""

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTER = ROOT / "hardware" / "common" / "unit-tester"
sys.path.insert(0, str(TESTER / "host"))

from christopher import WheelOrder  # noqa: E402
from christopher_tester import protocol, sim  # noqa: E402
from christopher_tester import reference as ref  # noqa: E402
from christopher_tester.diagnose import diagnose  # noqa: E402
from christopher_tester.protocol import coded_sources, Tester, TesterError, pack_pos  # noqa: E402
from christopher_tester.sweep import check_sweep  # noqa: E402

ORDER = "B II-IV-V"
L = ref.LETTERS


def run_scan(spec, faults=(), sides="AB", **kw):
    transport, hal = sim.make_sim(spec, faults, **kw)
    t = Tester(transport)
    return diagnose(spec, t.scan(sides), idle=t.sense()), t, hal


# --- reference model -------------------------------------------------------------------------------


def test_fast_scrambler_equals_christopher_everywhere_sampled():
    rng = random.Random(7)
    orders = ["B I-II-III", "C VI-VII-VIII", "B V-III-I", "C IV-I-VIII"]
    for order in orders:
        o = WheelOrder.parse(order)
        for _ in range(15):
            pos = [rng.randrange(26) for _ in range(3)]    # includes positions a real Enigma never shows
            assert ref.reference_scrambler(o, *pos) == ref.fast_scrambler(o.reflector, o.rotors, *pos)


def test_scrambler_is_involution_without_fixed_points():
    p = ref.reference_scrambler(ORDER, 3, 20, 11)
    assert all(p[p[i]] == i and p[i] != i for i in range(26))


def test_relay_model_equals_gear_reference():
    rng = random.Random(3)
    for _ in range(12):
        pos = [rng.randrange(26) for _ in range(3)]
        k = rng.randrange(26)
        gear = ref.unit_spec(ORDER, pos[0], pos[1], (pos[2] + k) % 26).perm   # edge offset k acts on the right drum
        relay = ref.relay_unit_spec(ORDER, *pos, k=k).perm
        assert relay == gear


def test_drum_spec_follows_pcb_convention():
    w = ref.rotor_wiring("III")
    s = ref.drum_spec("III", "A")
    assert s.perm == w
    assert (ref.a_node(4), ref.b_node(w[4])) in s.links or (ref.b_node(w[4]), ref.a_node(4)) in s.links
    assert s.label(ref.a_node(2)) == "4C" and s.label(ref.b_node(w[2])) == "3" + L[w[2]]
    assert ref.drum_spec("III", "B").label(ref.a_node(2)) == "1C"
    # drum shows letter D: contact j meets pad j+3
    s3 = ref.drum_spec("III", "A", offset=3)
    assert s3.perm[0] == (w[3] - 3) % 26


def test_shifter_and_stage_specs():
    assert ref.shifter_spec(7).perm[0] == 7
    assert ref.shifter_spec(7, reverse=True).perm[0] == 19
    assert ref.shifter_stage_spec(2, False).perm == list(range(26))
    assert ref.shifter_stage_spec(4, True).perm[10] == 0      # 10 + 16 mod 26


def test_position_sequence_matches_counter_rule():
    seq = list(ref.position_sequence(count=28))
    assert seq[25] == (25, 0, 0) and seq[26] == (0, 1, 0)
    big = ref.position_sequence()
    for _ in range(26 * 26):
        last = next(big)
    assert next(big) == (0, 0, 1) and last == (25, 25, 0)


# --- good units and faults diagnosed ----------------------------------------------------------------


def test_good_unit_passes():
    spec = ref.unit_spec(ORDER, 4, 9, 17)
    rep, _, hal = run_scan(spec)
    assert rep.ok, rep.render()
    assert rep.checked == 52
    assert hal.driven == 0 and hal.drive_log[-1] == 0         # drivers released


@pytest.mark.parametrize("spec", [
    ref.drum_spec("I", "A"), ref.drum_spec("V", "B", offset=7), ref.shifter_spec(13),
    ref.shifter_stage_spec(3, True), ref.relay_unit_spec("C II-I-III", 1, 2, 3, k=5), ref.reflector_spec("B"),
])
def test_other_good_specs_pass(spec):
    rep, _, _ = run_scan(spec)
    assert rep.ok, rep.render()


def test_open_contact_reported_with_both_lines():
    spec = ref.unit_spec(ORDER, 0, 0, 0)
    rep, _, _ = run_scan(spec, [sim.open_contact(5)])
    assert rep.kinds() == ["open"], rep.render()
    f = rep.findings[0]
    partner = spec.perm[5]
    assert "IN-F" in f.text and "OUT-" + L[partner] in f.text


def test_open_found_with_one_sided_scan_too():
    spec = ref.unit_spec(ORDER, 0, 0, 0)
    rep, _, _ = run_scan(spec, [sim.open_contact(5)], sides="A")
    assert rep.kinds() == ["open"]


def test_short_between_in_lines():
    spec = ref.unit_spec(ORDER, 2, 3, 4)
    rep, _, _ = run_scan(spec, [sim.short(ref.a_node(3), ref.a_node(9))])
    assert rep.kinds() == ["short"], rep.render()
    assert len(rep.findings) == 1
    text = rep.findings[0].text
    assert "IN-D" in text and "IN-J" in text


def test_short_between_in_and_out():
    spec = ref.unit_spec(ORDER, 2, 3, 4)
    k = (spec.perm[0] + 4) % 26
    rep, _, _ = run_scan(spec, [sim.short(ref.a_node(0), ref.b_node(k))])
    assert rep.kinds() == ["short"], rep.render()


def test_swapped_pair_reported_once():
    spec = ref.unit_spec(ORDER, 1, 1, 1)
    rep, _, _ = run_scan(spec, [sim.swap_pair(2, 11)])
    assert rep.kinds() == ["swap"], rep.render()
    assert len(rep.findings) == 1
    assert "IN-C" in rep.findings[0].text and "IN-L" in rep.findings[0].text
    rep_a, _, _ = run_scan(spec, [sim.swap_pair(2, 11)], sides="A")
    assert rep_a.kinds() == ["swap"]


def test_single_misroute():
    spec = ref.unit_spec(ORDER, 0, 0, 0)
    # A_0 goes to a free OUT line while its own partner is cut and another line becomes unconnected
    def reroute(net, s):
        net.disconnect(ref.a_node(0), ref.b_node(s.perm[0]))
        net.connect(ref.a_node(0), ref.b_node(s.perm[0] ^ 1 if s.perm[0] ^ 1 not in s.perm else (s.perm[0] + 1) % 26))
    rep, _, _ = run_scan(spec, [reroute])
    assert not rep.ok
    assert set(rep.kinds()) <= {"misroute", "short", "open"}


def test_wrong_rotor_identified():
    expected = ref.unit_spec(ORDER, 5, 6, 7)
    wrong = ref.unit_spec("B II-I-V", 5, 6, 7)
    transport, _ = sim.make_sim(network=sim.network_for(wrong))
    t = Tester(transport)
    rep = diagnose(expected, t.scan("AB"))
    assert not rep.ok
    assert any("wheel order B I-II-V" in s or "wheel order B II-I-V" in s for s in rep.identified), rep.render()
    assert "PASS" not in rep.render()


def test_wrong_reflector_identified():
    expected = ref.unit_spec("B I-II-III", 5, 6, 7)
    wrong = ref.unit_spec("C I-II-III", 5, 6, 7)
    rep = diagnose(expected, Tester(sim.make_sim(network=sim.network_for(wrong))[0]).scan("A"))
    assert any("wheel order C I-II-III" in s for s in rep.identified), rep.render()


def test_wrong_drum_position_located():
    expected = ref.unit_spec(ORDER, 0, 0, 0)
    wrong = ref.unit_spec(ORDER, 0, 0, 5)
    rep = diagnose(expected, Tester(sim.make_sim(network=sim.network_for(wrong))[0]).scan("A"))
    assert any("drum letters AAF" in s for s in rep.identified), rep.render()


def test_wrong_drum_board_identified():
    expected = ref.drum_spec("III", "A")
    for wrong_rotor, copy in (("IV", "A"), ("V", "B")):
        wrong = ref.drum_spec(wrong_rotor, copy)
        rep = diagnose(expected, Tester(sim.make_sim(network=sim.network_for(wrong))[0]).scan("AB"))
        assert not rep.ok
        assert any("rotor %s at drum letter A" % wrong_rotor in s for s in rep.identified), rep.render()


def test_drum_mounted_rotated_identified():
    rep = diagnose(ref.drum_spec("III", "A", 0),
                   Tester(sim.make_sim(network=sim.network_for(ref.drum_spec("III", "A", 4)))[0]).scan("A"))
    assert any("rotor III at drum letter E" in s for s in rep.identified), rep.render()


def test_wrong_shifter_control_identified():
    rep = diagnose(ref.shifter_spec(5), Tester(sim.make_sim(network=sim.network_for(ref.shifter_spec(6)))[0]).scan("A"))
    assert any("control 6" in s for s in rep.identified), rep.render()


def test_stage_stuck_released():
    # stage 1 (2 places) should be active but its relay is stuck released: unit behaves as identity
    rep, _, _ = run_scan(ref.shifter_stage_spec(1, True), [lambda net, s: None], sides="A")
    assert rep.ok
    wrong = sim.network_for(ref.shifter_stage_spec(1, False))
    rep = diagnose(ref.shifter_stage_spec(1, True), Tester(sim.make_sim(network=wrong)[0]).scan("A"))
    assert not rep.ok


def test_dead_line_and_stuck_idle_line():
    spec = ref.shifter_spec(0)
    obs = {n: frozenset(spec.components()[n]) for n in range(26)}
    obs[4] = frozenset()                                  # driven line 4 does not read back
    rep = diagnose(spec, obs, idle=frozenset({9}))
    assert {"dead", "stuck"} <= set(rep.kinds())


# --- firmware logic ---------------------------------------------------------------------------------------


def test_protocol_basics_and_errors():
    spec = ref.shifter_spec(1)
    transport, hal = sim.make_sim(spec)
    t = Tester(transport)
    assert t.ident().startswith("christopher-unit-tester")
    assert t.sense() == frozenset()
    assert t.drive("A", 0) == frozenset({0, 26 + 1})
    assert t.drive("B", 1) == frozenset({0, 26 + 1})
    for bad in ("DRIVE A 26", "DRIVE C 1", "DRIVE A", "SCAN X", "FROB", "SETTLE 99999999", "RUN 2"):
        with pytest.raises(TesterError):
            t.command(bad)
    assert hal.driven == 0
    t.settle(250)
    assert hal.settle_us == 250
    t.set_run(True)
    t.set_home(True)
    assert hal.run == 1 and hal.home == 1
    pos, flags = t.aux()
    assert flags & protocol.F_ESTOP_OK


def test_interlock_blocks_driving():
    transport, hal = sim.make_sim(ref.shifter_spec(1), flags=protocol.F_ESTOP_OK)    # ILOCK open
    t = Tester(transport)
    for cmd in ("DRIVE A 0", "SCAN A", "SWEEP 1 A", "RUN 1"):
        with pytest.raises(TesterError, match="interlock"):
            t.command(cmd)
    assert not any(hal.drive_log)
    t.command("RUN 0")


def test_scan_masks_are_one_per_line():
    transport, _ = sim.make_sim(ref.unit_spec(ORDER, 0, 0, 0))
    t = Tester(transport)
    obs = t.scan("A")
    assert sorted(obs) == list(range(26)) and all(len(v) == 2 for v in obs.values())
    assert len(t.scan("AB")) == 52


def test_adc_contact_resistance_estimate():
    assert (protocol.V_DRIVE, protocol.R_SERIES, protocol.R_LED, protocol.V_LED, protocol.ADC_RATIO) == (
        sim._firmware()[0].V_DRIVE, sim._firmware()[0].R_SERIES, sim._firmware()[0].R_LED,
        sim._firmware()[0].V_LED, sim._firmware()[0].ADC_RATIO)
    spec = ref.shifter_spec(0)
    for ohm in (0.05, 20.0, 100.0):
        transport, _ = sim.make_sim(spec, [sim.high_resistance(3, ohm)])
        r = Tester(transport).resistance("A", 3, "B", 3)
        assert abs(r - ohm) < 8.0, (ohm, r)
    transport, _ = sim.make_sim(spec, [sim.open_contact(3)])
    assert Tester(transport).resistance("A", 3, "B", 3) is None


# --- STROBE sweep ---------------------------------------------------------------------------------------------


def positions(n, start=(0, 0, 0)):
    return list(ref.position_sequence(start, n))


def test_sweep_good_unit_passes_and_reports_timing():
    pos = positions(30, (20, 25, 3))
    transport, _ = sim.make_sim(sequence=sim.unit_sequence(ORDER, pos))
    sw = Tester(transport).sweep(30, "A")
    assert len(sw.samples) == 30 and not sw.timed_out
    assert [protocol.unpack_pos(s.pos) for s in sw.samples] == pos
    rep = check_sweep(ORDER, sw)
    assert rep.ok, rep.render()
    assert len(rep.positions) == 30 and not rep.duplicates and not rep.late
    assert set(rep.widths) == {6000}


def test_sweep_flags_fault_at_one_position():
    pos = positions(12)
    seq = sim.unit_sequence(ORDER, pos, {7: [sim.open_contact(3)]})
    transport, _ = sim.make_sim(sequence=seq)
    rep = check_sweep(ORDER, Tester(transport).sweep(12, "A"))
    assert not rep.ok and len(rep.failures) == 1
    assert rep.failures[0][0] == pos[7] and rep.failures[0][1].kinds() == ["open"]
    assert "position " + "".join(L[v] for v in pos[7]) in rep.render()


def test_sweep_wrong_position_report_is_mismatch():
    # unit reports POS 0,0,1 but conducts like 0,0,0
    good = ref.unit_spec(ORDER, 0, 0, 0)
    seq = [(pack_pos(0, 0, 1), sim.network_for(good))]
    transport, _ = sim.make_sim(sequence=seq)
    rep = check_sweep(ORDER, Tester(transport).sweep(1, "A"))
    assert not rep.ok


def test_sweep_timeout_and_end_and_late_scan():
    pos = positions(3)
    transport, _ = sim.make_sim(sequence=sim.unit_sequence(ORDER, pos))
    sw = Tester(transport).sweep(5, "A", timeout_ms=50)
    assert sw.timed_out and len(sw.samples) == 3
    assert not check_sweep(ORDER, sw).ok

    transport, hal = sim.make_sim(sequence=sim.unit_sequence(ORDER, positions(10)))
    hal.end_after = 4
    sw = Tester(transport).sweep(10, "A")
    assert sw.ended_early and len(sw.samples) == 4

    transport, _ = sim.make_sim(sequence=sim.unit_sequence(ORDER, positions(2)), strobe_width_us=1000)
    rep = check_sweep(ORDER, Tester(transport).sweep(2, "A"))      # 26 x 150 us = 3.9 ms > 1 ms
    assert rep.late and not rep.ok


def test_sweep_without_scan_only_reads_position():
    transport, _ = sim.make_sim(sequence=sim.unit_sequence(ORDER, positions(3)))
    sw = Tester(transport).sweep(3, "-")
    assert [s.masks for s in sw.samples] == [{}, {}, {}]


def test_cli_scan_on_simulated_unit(capsys):
    from christopher_tester.__main__ import main

    assert main(["--sim", "scan", "--unit", ORDER, "--pos", "ABC"]) == 0
    assert "PASS" in capsys.readouterr().out
    assert main(["--sim", "--fault", "open:3", "scan", "--unit", ORDER, "--pos", "ABC"]) == 1
    assert "OPEN" in capsys.readouterr().out


# --- coded scan (5 drive planes instead of 26) --------------------------------------------------------------


def test_coded_scan_decodes_permutation():
    spec = ref.unit_spec(ORDER, 3, 4, 5)
    transport, hal = sim.make_sim(spec)
    planes = Tester(transport).cscan("A")
    src = coded_sources(planes, "A")
    inv = [spec.perm.index(j) for j in range(26)]
    assert src == inv
    assert hal.driven == 0
    # open line leaves its far end unreached; swap changes two sources
    transport, _ = sim.make_sim(spec, [sim.open_contact(7)])
    assert coded_sources(Tester(transport).cscan("A"), "A")[spec.perm[7]] is None


def test_coded_sweep_passes_and_flags_faults_quickly():
    pos = positions(10)
    transport, _ = sim.make_sim(sequence=sim.unit_sequence(ORDER, pos, {4: [sim.swap_pair(1, 8)]}))
    sw = Tester(transport).sweep(10, "CA")
    assert all(s.planes and not s.masks and s.scan_us == 5 * 150 for s in sw.samples)
    rep = check_sweep(ORDER, sw)
    assert [p for p, _ in rep.failures] == [pos[4]] and not rep.late
    transport, _ = sim.make_sim(sequence=sim.unit_sequence(ORDER, pos))
    assert check_sweep(ORDER, Tester(transport).sweep(10, "CA")).ok


def test_cscan_bad_side():
    transport, _ = sim.make_sim(ref.shifter_spec(0))
    with pytest.raises(TesterError):
        Tester(transport).command("CSCAN X")


def test_drum_sweep_through_26_letters_and_isolation():
    seq = [(pack_pos(o, 0, 0), sim.network_for(ref.drum_spec("III", "A", o))) for o in range(26)]
    transport, _ = sim.make_sim(sequence=seq)
    sw = Tester(transport).sweep(26, "A")
    assert check_sweep("drum III", sw, spec_for=lambda p: ref.drum_spec("III", "A", p[0])).ok
    # one worn pad at letter K (pad 4 of the copy is dead)
    seq = [(pack_pos(o, 0, 0), sim.network_for(ref.drum_spec("III", "A", o),
                                               [sim.open_contact(4)] if o == 10 else ())) for o in range(26)]
    transport, _ = sim.make_sim(sequence=seq)
    rep = check_sweep("drum III", Tester(transport).sweep(26, "A"), spec_for=lambda p: ref.drum_spec("III", "A", p[0]))
    assert [p[0] for p, _ in rep.failures] == [10]
    # copy A against copy B must be isolated; a bridge between pad 4C and pad 1H is a short
    iso = ref.isolation_spec("ring4/ring1", "4", "1")
    good, _ = sim.make_sim(iso)
    assert diagnose(iso, Tester(good).scan("AB")).ok
    bad, _ = sim.make_sim(iso, [sim.short(ref.a_node(2), ref.b_node(7))])
    r = diagnose(iso, Tester(bad).scan("AB"))
    assert r.kinds() == ["short"] and "4-C" in r.findings[0].text and "1-H" in r.findings[0].text
