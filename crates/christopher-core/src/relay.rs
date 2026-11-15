//! Reference model of the relay scrambler (Phase 3, relay track — `hardware/relay/concept.md`).
//!
//! A rotor at core offset `s` maps `c ↦ W[c + s] − s`, i.e. "shift by +s, fixed wiring W, shift by
//! −s". Chaining right → middle → left, neighbouring shifts merge, so the outbound half of a
//! scrambler is
//!
//! ```text
//! S(a1) · W_R · S(a2) · W_M · S(a3) · W_L · S(a4)      (in signal order)
//! a1 = r + k     a2 = m − (r + k)     a3 = l − m     a4 = −l
//! ```
//!
//! where `k` is the unit's crib offset (applied to the right rotor, as on the real bombe). The
//! return half is the same board used backwards (each shifter run in reverse shifts by −a, each
//! wiring card read backwards is W⁻¹). Only four shifters per half are needed, and the control
//! values are plain up-counters that either advance or hold ([`Counters`]), so the machine needs
//! no subtractors. Each shifter is five binary stages (1, 2, 4, 8, 16 places; [`shift_binary`]).
//!
//! This module is the golden model for the relay hardware: the tests prove it equal to the table
//! scrambler ([`crate::bombe::WheelOrder::scrambler`]) at every position.

use crate::alphabet::{self, N};
use crate::bombe::WheelOrder;

/// Shift letter `c` by `s` places (mod 26).
#[inline]
fn shift(c: u8, s: u8) -> u8 {
    alphabet::add(c, s)
}

/// One binary shifter: five stages of 1, 2, 4, 8, 16 places, stage `i` active when bit `i` of the
/// 5-bit control is set. `reverse` = the module mounted backwards (shifts by −value). Controls are
/// 0..=25, so the stage sum never exceeds 25 and each stage is a fixed rewiring of 26 lines.
pub fn shift_binary(c: u8, control: u8, reverse: bool) -> u8 {
    debug_assert!(control < 26);
    let mut x = c;
    for i in 0..5 {
        if control >> i & 1 == 1 {
            let step = 1u8 << i;
            x = if reverse { alphabet::sub(x, step) } else { shift(x, step) };
        }
    }
    x
}

/// The four control values of one scrambler unit.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Controls {
    pub a1: u8,
    pub a2: u8,
    pub a3: u8,
    /// Applied by a shifter mounted in reverse: the hardware counts `l`, the shift is −l.
    pub a4_l: u8,
}

impl Controls {
    /// Controls for drum cores (left, middle, right) and crib offset `k`.
    pub fn from_cores(l: u8, m: u8, r: u8, k: u8) -> Controls {
        let rk = alphabet::add(r, k % N as u8);
        Controls { a1: rk, a2: alphabet::sub(m, rk), a3: alphabet::sub(l, m), a4_l: l }
    }
}

/// Relay scrambler: the four rotor/reflector wiring cards of a wheel order plus shifters.
#[derive(Debug, Clone, Copy)]
pub struct RelayScrambler {
    order: WheelOrder,
}

impl RelayScrambler {
    /// Three-rotor wheel orders only (the physical machines do not model the greek wheel).
    pub fn new(order: WheelOrder) -> Option<RelayScrambler> {
        order.greek.is_none().then_some(RelayScrambler { order })
    }

    /// Signal path through the unit: outbound half, reflector card, return half.
    pub fn scramble(&self, a: Controls, c: u8) -> u8 {
        let [lw, mw, rw] = self.order.rotors;
        // Outbound half: IN → S(a1) → W_R → S(a2) → W_M → S(a3) → W_L → S(−l).
        let mut x = shift_binary(c, a.a1, false);
        x = rw.forward[x as usize];
        x = shift_binary(x, a.a2, false);
        x = mw.forward[x as usize];
        x = shift_binary(x, a.a3, false);
        x = lw.forward[x as usize];
        x = shift_binary(x, a.a4_l, true);
        // Reflector card.
        x = self.order.reflector.map[x as usize];
        // Return half = the same board read from the other end.
        x = shift_binary(x, a.a4_l, false);
        x = lw.backward[x as usize];
        x = shift_binary(x, a.a3, true);
        x = mw.backward[x as usize];
        x = shift_binary(x, a.a2, true);
        x = rw.backward[x as usize];
        shift_binary(x, a.a1, true)
    }

    pub fn permutation(&self, a: Controls) -> [u8; N] {
        std::array::from_fn(|c| self.scramble(a, c as u8))
    }
}

/// Counter bank of the relay machine for one unit (crib offset `k`).
///
/// The fast position is the left core (as on the British bombe's top drum). Every clock: `l`
/// advances; on its 25 → 0 carry `m` advances; on that one's carry `r` advances. The difference
/// controls are kept as up-counters that advance or hold — never decrement:
///
/// | counter | advances when                               |
/// |---------|---------------------------------------------|
/// | a4 (=l) | every clock                                 |
/// | a3      | every clock except when `l` wraps           |
/// | a2      | when `m` advances, except when `m` wraps     |
/// | a1      | when `m` wraps (i.e. `r` advances)          |
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Counters {
    pub l: u8,
    pub m: u8,
    pub r: u8,
    pub k: u8,
    pub controls: Controls,
}

impl Counters {
    /// Preset at the home position (l = m = r = 0) — the offset dial sets a1 = k, a2 = −k.
    pub fn home(k: u8) -> Counters {
        Counters { l: 0, m: 0, r: 0, k, controls: Controls::from_cores(0, 0, 0, k) }
    }

    /// One clock, using only increments (mod 26) and holds.
    pub fn clock(&mut self) {
        let l_wraps = self.l == 25;
        let m_advances = l_wraps;
        let m_wraps = m_advances && self.m == 25;
        let c = &mut self.controls;
        c.a4_l = shift(c.a4_l, 1);
        if !l_wraps {
            c.a3 = shift(c.a3, 1);
        }
        if m_advances && !m_wraps {
            c.a2 = shift(c.a2, 1);
        }
        if m_wraps {
            c.a1 = shift(c.a1, 1);
        }
        self.l = shift(self.l, 1);
        if m_advances {
            self.m = shift(self.m, 1);
        }
        if m_wraps {
            self.r = shift(self.r, 1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bombe::Position;
    use crate::enigma::Model;

    #[test]
    fn binary_shifter_is_mod26_shift() {
        for c in 0..26u8 {
            for s in 0..26u8 {
                assert_eq!(shift_binary(c, s, false), alphabet::add(c, s));
                assert_eq!(shift_binary(c, s, true), alphabet::sub(c, s));
            }
        }
    }

    /// Every Enigma I wheel order, every one of the 17,576 positions, several crib offsets:
    /// the relay network equals the table scrambler used by the bombe model.
    #[test]
    fn relay_scrambler_equals_table_scrambler_everywhere() {
        for order in WheelOrder::all(Model::EnigmaI) {
            let relay = RelayScrambler::new(order).unwrap();
            for k in [0u8, 7, 25] {
                for l in 0..26u8 {
                    for m in 0..26u8 {
                        let table = order.scramblers(0, l, m);
                        for r in 0..26u8 {
                            let want = table[alphabet::add(r, k) as usize];
                            let got = relay.permutation(Controls::from_cores(l, m, r, k));
                            assert_eq!(got, want, "{} l={l} m={m} r={r} k={k}", order.name());
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn navy_rotors_too() {
        let order = WheelOrder::new("C", None, ["VIII", "VI", "VII"]).unwrap();
        let relay = RelayScrambler::new(order).unwrap();
        for (l, m, r) in [(0, 0, 0), (25, 13, 7), (12, 25, 25), (3, 9, 22)] {
            let p = Position { greek: 0, left: l, middle: m, right: r };
            assert_eq!(relay.permutation(Controls::from_cores(l, m, r, 0)), order.scrambler(p));
        }
        assert!(RelayScrambler::new(WheelOrder::new("B-THIN", Some("BETA"), ["I", "II", "III"]).unwrap()).is_none());
    }

    /// The increment-or-hold counter rules reproduce the controls over a whole run (17,576 clocks
    /// and the wrap back to home), for every crib offset.
    #[test]
    fn counters_track_controls_over_full_run() {
        for k in 0..26u8 {
            let mut c = Counters::home(k);
            for step in 0..=17_576u32 {
                assert_eq!(c.controls, Controls::from_cores(c.l, c.m, c.r, k), "k={k} step={step}");
                c.clock();
            }
        }
    }
}
