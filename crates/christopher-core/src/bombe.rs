//! The Turing–Welchman Bombe.
//!
//! Machine model:
//! * one **26-wire cable** per letter; wire `w` of cable `a` live means "hypothesis S(a) = w"
//!   (S = the unknown plugboard);
//! * one **scrambler** (drum set = Enigma rotors + reflector, no plugboard) per menu edge, joining
//!   the cables of the edge's two letters, set `pos` steps after the first crib letter;
//! * the **diagonal board** joins wire `w` of cable `a` to wire `a` of cable `w` (S is symmetric);
//! * the **test register** watches the cable of the test letter; one wire receives the current.
//!
//! Current spreads until nothing changes. If the test register is fully live, the hypothesis
//! and every alternative are contradicted: no stop. Otherwise the bombe **stops**.
//!
//! The state is 26 cables × 26 wires = 676 bits (`[u32; 26]`), the same register the RTL will hold.

use crate::alphabet::{self, FULL, N};
use crate::enigma::{through, Model};
use crate::error::Result;
use crate::menu::Menu;
use crate::wiring::{self, ReflectorSpec, RotorSpec};

/// Reflector + greek wheel (M4) + three rotors, left to right.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WheelOrder {
    pub reflector: &'static ReflectorSpec,
    pub greek: Option<&'static RotorSpec>,
    pub rotors: [&'static RotorSpec; 3],
}

impl WheelOrder {
    pub fn new(reflector: &str, greek: Option<&str>, rotors: [&str; 3]) -> Result<WheelOrder> {
        Ok(WheelOrder {
            reflector: wiring::reflector(reflector)?,
            greek: greek.map(wiring::rotor).transpose()?,
            rotors: [wiring::rotor(rotors[0])?, wiring::rotor(rotors[1])?, wiring::rotor(rotors[2])?],
        })
    }

    /// e.g. `"B I-II-III"` or `"B-THIN BETA II-IV-I"`.
    pub fn name(&self) -> String {
        let rotors = self.rotors.iter().map(|r| r.name.as_str()).collect::<Vec<_>>().join("-");
        match self.greek {
            Some(g) => format!("{} {} {}", self.reflector.name, g.name, rotors),
            None => format!("{} {}", self.reflector.name, rotors),
        }
    }

    /// Every wheel order for a model: ordered choices of 3 distinct rotors × reflectors × greek wheels.
    /// Enigma I: 60 × 2, M3: 336 × 2, M4: 336 × 2 × 2.
    pub fn all(model: Model) -> Vec<WheelOrder> {
        let names = model.rotor_names();
        let greeks: Vec<Option<&str>> =
            if model.has_greek() { model.greek_names().iter().map(|&g| Some(g)).collect() } else { vec![None] };
        let mut out = Vec::new();
        for &refl in model.reflector_names() {
            for &g in &greeks {
                for &a in names {
                    for &b in names {
                        for &c in names {
                            if a != b && b != c && a != c {
                                out.push(WheelOrder::new(refl, g, [a, b, c]).expect("built-in names"));
                            }
                        }
                    }
                }
            }
        }
        out
    }

    /// Scrambler with only the right drum varying: `inner` covers middle → left → greek → reflector
    /// and back for fixed (greek, left, middle) core offsets.
    fn inner(&self, g: u8, l: u8, m: u8) -> [u8; N] {
        let [lr, mr, _] = self.rotors;
        std::array::from_fn(|c| {
            let mut x = through(&mr.forward, m, c as u8);
            x = through(&lr.forward, l, x);
            if let Some(gr) = self.greek {
                x = through(&gr.forward, g, x);
            }
            x = self.reflector.map[x as usize];
            if let Some(gr) = self.greek {
                x = through(&gr.backward, g, x);
            }
            x = through(&lr.backward, l, x);
            through(&mr.backward, m, x)
        })
    }

    /// The 26 scramblers obtained by turning the right drum through all positions.
    pub fn scramblers(&self, g: u8, l: u8, m: u8) -> [[u8; N]; N] {
        let inner = self.inner(g, l, m);
        let rr = self.rotors[2];
        std::array::from_fn(|r| {
            std::array::from_fn(|c| {
                let x = through(&rr.forward, r as u8, c as u8);
                through(&rr.backward, r as u8, inner[x as usize])
            })
        })
    }

    /// Scrambler permutation (no plugboard) at the given core offsets.
    pub fn scrambler(&self, p: Position) -> [u8; N] {
        self.scramblers(p.greek, p.left, p.middle)[p.right as usize]
    }
}

/// Bombe drum position: core offsets (window − ring) of the Enigma rotors at the moment the
/// **first crib letter** is enciphered. Edge `pos = k` uses the right core `right + k`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub struct Position {
    pub greek: u8,
    pub left: u8,
    pub middle: u8,
    pub right: u8,
}

impl Position {
    pub fn to_letters(&self, with_greek: bool) -> String {
        let mut v = Vec::with_capacity(4);
        if with_greek {
            v.push(self.greek);
        }
        v.extend([self.left, self.middle, self.right]);
        alphabet::to_string(&v)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BombeOptions {
    /// Welchman's diagonal board (true for the real Turing–Welchman bombe).
    pub diagonal_board: bool,
    /// Assume the middle rotor steps when crib position `t` is enciphered (edges with `pos >= t`
    /// use middle + 1). `None` = no turnover inside the crib (the standard bombe assumption).
    /// A double step of the middle rotor is not modelled.
    pub turnover_at: Option<u16>,
}

impl Default for BombeOptions {
    fn default() -> Self {
        BombeOptions { diagonal_board: true, turnover_at: None }
    }
}

/// A bombe stop.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Stop {
    pub wheel_order: WheelOrder,
    pub position: Position,
    /// Live wires of the test register (bitmask).
    pub test_register: u32,
    pub turnover_at: Option<u16>,
}

impl Stop {
    /// Stecker values for the test letter suggested by the register: the single live wire, or the
    /// single dead wire when 25 are live; otherwise every dead wire.
    pub fn stecker_candidates(&self) -> Vec<u8> {
        let live = self.test_register;
        match live.count_ones() {
            1 => vec![live.trailing_zeros() as u8],
            _ => (0..N as u8).filter(|&w| live >> w & 1 == 0).collect(),
        }
    }
}

/// The menu wired into the bombe ("plugged up").
#[derive(Debug, Clone)]
pub struct Plugged {
    /// For each cable: (other cable, edge index).
    adj: Vec<Vec<(u8, usize)>>,
    /// Crib position of each edge.
    edge_pos: Vec<u16>,
    pub test_letter: u8,
    pub input_wire: u8,
}

impl Plugged {
    pub fn new(menu: &Menu) -> Plugged {
        let mut adj = vec![Vec::new(); N];
        for (i, e) in menu.edges.iter().enumerate() {
            adj[e.plain as usize].push((e.cipher, i));
            adj[e.cipher as usize].push((e.plain, i));
        }
        Plugged {
            adj,
            edge_pos: menu.edges.iter().map(|e| e.pos).collect(),
            test_letter: menu.test_letter,
            input_wire: menu.input_wire,
        }
    }

    pub fn edges(&self) -> usize {
        self.edge_pos.len()
    }

    /// For each edge, the index into a `[normal 26 | turned-over 26]` scrambler table.
    fn edge_indices(&self, right: u8, turnover_at: Option<u16>, out: &mut [usize]) {
        for (i, &k) in self.edge_pos.iter().enumerate() {
            let r = (right as usize + k as usize) % N;
            out[i] = match turnover_at {
                Some(t) if k >= t => N + r,
                _ => r,
            };
        }
    }

    /// Spread current from `(cable, wire)` until stable. With `stop_when_full`, return early as
    /// soon as the test register is fully live. Returns the live wires of every cable.
    pub fn propagate(
        &self,
        tables: &[[u8; N]],
        edge_table: &[usize],
        start: (u8, u8),
        diagonal: bool,
        stop_when_full: bool,
    ) -> [u32; N] {
        let mut live = [0u32; N];
        let mut stack: [(u8, u8); N * N] = [(0, 0); N * N];
        let mut sp = 0usize;
        let test = self.test_letter as usize;
        live[start.0 as usize] |= 1 << start.1;
        stack[sp] = start;
        sp += 1;
        while sp > 0 {
            sp -= 1;
            let (a, w) = stack[sp];
            for &(b, e) in &self.adj[a as usize] {
                let w2 = tables[edge_table[e]][w as usize];
                let bit = 1u32 << w2;
                if live[b as usize] & bit == 0 {
                    live[b as usize] |= bit;
                    stack[sp] = (b, w2);
                    sp += 1;
                    if stop_when_full && b as usize == test && live[test] == FULL {
                        return live;
                    }
                }
            }
            if diagonal {
                let bit = 1u32 << a;
                if live[w as usize] & bit == 0 {
                    live[w as usize] |= bit;
                    stack[sp] = (w, a);
                    sp += 1;
                    if stop_when_full && w as usize == test && live[test] == FULL {
                        return live;
                    }
                }
            }
        }
        live
    }

    /// Scrambler tables for (greek, left, middle): normal and, if needed, with the middle turned over.
    pub fn tables(order: &WheelOrder, g: u8, l: u8, m: u8, turnover: bool) -> Vec<[u8; N]> {
        let mut t = order.scramblers(g, l, m).to_vec();
        if turnover {
            t.extend_from_slice(&order.scramblers(g, l, alphabet::add(m, 1)));
        }
        t
    }

    /// Full state of all 676 wires at one position (no early exit) — for tracing, RTL vectors
    /// and the checking stage.
    pub fn trace(&self, order: &WheelOrder, p: Position, input_wire: u8, opts: &BombeOptions) -> [u32; N] {
        let tables = Plugged::tables(order, p.greek, p.left, p.middle, opts.turnover_at.is_some());
        let mut idx = vec![0usize; self.edges()];
        self.edge_indices(p.right, opts.turnover_at, &mut idx);
        self.propagate(&tables, &idx, (self.test_letter, input_wire), opts.diagonal_board, false)
    }
}

/// Result of running one wheel order through all drum positions.
#[derive(Debug, Clone)]
pub struct RunResult {
    pub stops: Vec<Stop>,
    pub positions: u64,
}

/// Run the bombe for one wheel order over every drum position (26³, or 26⁴ with a greek wheel).
pub fn run(menu: &Menu, order: &WheelOrder, opts: &BombeOptions) -> RunResult {
    let plugged = Plugged::new(menu);
    run_plugged(&plugged, order, opts)
}

pub fn run_plugged(plugged: &Plugged, order: &WheelOrder, opts: &BombeOptions) -> RunResult {
    run_slice(plugged, order, opts, None)
}

/// Like [`run_plugged`], but with `greek = Some(g)` only the 26³ positions with greek core `g`
/// (lets callers spread a four-rotor run over several threads). Ignored without a greek wheel.
pub fn run_slice(plugged: &Plugged, order: &WheelOrder, opts: &BombeOptions, greek: Option<u8>) -> RunResult {
    let greeks = match (order.greek, greek) {
        (None, _) => 0..1,
        (Some(_), Some(g)) => g..g + 1,
        (Some(_), None) => 0..N as u8,
    };
    let mut stops = Vec::new();
    let mut idx = vec![0usize; plugged.edges()];
    let mut positions = 0u64;
    let start = (plugged.test_letter, plugged.input_wire);
    for g in greeks {
        for l in 0..N as u8 {
            for m in 0..N as u8 {
                let tables = Plugged::tables(order, g, l, m, opts.turnover_at.is_some());
                for r in 0..N as u8 {
                    positions += 1;
                    plugged.edge_indices(r, opts.turnover_at, &mut idx);
                    let live = plugged.propagate(&tables, &idx, start, opts.diagonal_board, true);
                    let reg = live[plugged.test_letter as usize];
                    if reg != FULL {
                        stops.push(Stop {
                            wheel_order: *order,
                            position: Position { greek: g, left: l, middle: m, right: r },
                            test_register: reg,
                            turnover_at: opts.turnover_at,
                        });
                    }
                }
            }
        }
    }
    RunResult { stops, positions }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::alphabet::clean;
    use crate::enigma::{Enigma, Key};

    #[test]
    fn scrambler_matches_enigma() {
        let key = Key::parse("m4", "B-THIN", Some("BETA"), ["II", "IV", "I"], "AAAA", "CXQD", "").unwrap();
        let mut e = Enigma::new(&key).unwrap();
        let order = WheelOrder::new("B-THIN", Some("BETA"), ["II", "IV", "I"]).unwrap();
        for _ in 0..40 {
            e.step();
            let c = e.cores();
            let p = Position { greek: c[0], left: c[1], middle: c[2], right: c[3] };
            let s = order.scrambler(p);
            for (x, &out) in s.iter().enumerate() {
                assert_eq!(out, e.scramble(x as u8));
            }
        }
    }

    #[test]
    fn finds_true_position() {
        let key = Key::parse("enigma-i", "B", None, ["II", "V", "III"], "AAA", "LCA", "AQ BZ CR DM EV FW GK HN IU JP").unwrap();
        let plain = clean("WETTERVORHERSAGEFUERDIENORDSEEXHEUTEKLARXWIND");
        let cipher = Enigma::new(&key).unwrap().process(&plain);
        let crib = &plain[..16];
        let menu = Menu::from_crib(&cipher, crib, 0).unwrap();
        let order = WheelOrder::new("B", None, ["II", "V", "III"]).unwrap();
        let res = run(&menu, &order, &BombeOptions::default());
        assert_eq!(res.positions, 17576);
        // First crib letter is enciphered after one step: right core A -> B.
        let want = Position { greek: 0, left: 11, middle: 2, right: 1 };
        assert!(res.stops.iter().any(|s| s.position == want), "stops: {}", res.stops.len());
    }
}
