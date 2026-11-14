//! Enigma I (Army/Air Force), M3 and M4 (Navy).
//!
//! Signal path for one key press (after stepping):
//! plugboard → right → middle → left → [greek] → reflector → [greek] → left → middle → right → plugboard.

use crate::alphabet::{self, N};
use crate::error::{Error, Result};
use crate::wiring::{self, ReflectorSpec, RotorSpec};

/// Machine model; determines which rotors and reflectors are allowed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Model {
    /// Wehrmacht/Luftwaffe Enigma I: rotors I–V, reflector B or C.
    EnigmaI,
    /// Kriegsmarine M3: rotors I–VIII, reflector B or C.
    M3,
    /// Kriegsmarine M4: rotors I–VIII + greek wheel (Beta/Gamma), thin reflector B or C.
    M4,
}

impl Model {
    pub fn parse(s: &str) -> Result<Model> {
        match s.trim().to_ascii_lowercase().replace(['_', ' '], "-").as_str() {
            "enigma-i" | "i" | "army" | "1" | "enigma1" | "enigma-1" => Ok(Model::EnigmaI),
            "m3" | "navy3" => Ok(Model::M3),
            "m4" | "navy4" | "shark" => Ok(Model::M4),
            _ => Err(Error::Config(format!("unknown model {s:?} (use enigma-i, m3, m4)"))),
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Model::EnigmaI => "enigma-i",
            Model::M3 => "m3",
            Model::M4 => "m4",
        }
    }

    pub fn rotor_names(self) -> &'static [&'static str] {
        match self {
            Model::EnigmaI => &["I", "II", "III", "IV", "V"],
            Model::M3 | Model::M4 => &["I", "II", "III", "IV", "V", "VI", "VII", "VIII"],
        }
    }

    pub fn reflector_names(self) -> &'static [&'static str] {
        match self {
            Model::EnigmaI | Model::M3 => &["B", "C"],
            Model::M4 => &["B-THIN", "C-THIN"],
        }
    }

    pub fn greek_names(self) -> &'static [&'static str] {
        match self {
            Model::M4 => &["BETA", "GAMMA"],
            _ => &[],
        }
    }

    pub fn has_greek(self) -> bool {
        self == Model::M4
    }
}

/// A rotor mounted in the machine.
#[derive(Debug, Clone)]
pub struct Rotor {
    pub spec: &'static RotorSpec,
    /// Ring setting (Ringstellung), 0 = A.
    pub ring: u8,
    /// Letter showing in the window, 0 = A.
    pub pos: u8,
}

impl Rotor {
    pub fn new(spec: &'static RotorSpec, ring: u8, pos: u8) -> Rotor {
        Rotor { spec, ring: ring % 26, pos: pos % 26 }
    }

    /// Core offset: how far the wiring core is rotated (window position minus ring setting).
    #[inline]
    pub fn core(&self) -> u8 {
        alphabet::sub(self.pos, self.ring)
    }

    #[inline]
    pub fn forward(&self, c: u8) -> u8 {
        through(&self.spec.forward, self.core(), c)
    }

    #[inline]
    pub fn backward(&self, c: u8) -> u8 {
        through(&self.spec.backward, self.core(), c)
    }

    #[inline]
    pub fn at_turnover(&self) -> bool {
        self.spec.turnovers.contains(&self.pos)
    }

    #[inline]
    pub fn advance(&mut self) {
        self.pos = alphabet::add(self.pos, 1);
    }
}

/// Pass letter `c` through a wiring table whose core is rotated by `core`.
#[inline]
pub fn through(table: &[u8; N], core: u8, c: u8) -> u8 {
    let s = core as usize;
    let out = table[(c as usize + s) % N] as usize;
    ((out + N - s) % N) as u8
}

/// Steckerbrett: an involution on the 26 letters.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Plugboard {
    map: [u8; N],
}

impl Default for Plugboard {
    fn default() -> Self {
        Plugboard::identity()
    }
}

impl Plugboard {
    pub fn identity() -> Plugboard {
        let mut map = [0u8; N];
        for (i, m) in map.iter_mut().enumerate() {
            *m = i as u8;
        }
        Plugboard { map }
    }

    /// Parse pairs such as `"AV BS CG"` (spaces optional between pairs: `"AVBSCG"` works too).
    pub fn from_pairs(pairs: &str) -> Result<Plugboard> {
        let letters = alphabet::parse_letters(pairs)?;
        if letters.len() % 2 != 0 {
            return Err(Error::Parse(format!("plugboard needs letter pairs: {pairs:?}")));
        }
        let mut pb = Plugboard::identity();
        for p in letters.chunks(2) {
            pb.connect(p[0], p[1])?;
        }
        Ok(pb)
    }

    pub fn from_map(map: [u8; N]) -> Result<Plugboard> {
        for (i, &o) in map.iter().enumerate() {
            if o as usize >= N || map[o as usize] as usize != i {
                return Err(Error::Config("plugboard map is not an involution".into()));
            }
        }
        Ok(Plugboard { map })
    }

    /// Connect two (currently self-steckered) letters.
    pub fn connect(&mut self, a: u8, b: u8) -> Result<()> {
        if a == b {
            return Err(Error::Config(format!("cannot stecker {} to itself", alphabet::to_char(a))));
        }
        if self.map[a as usize] != a || self.map[b as usize] != b {
            return Err(Error::Config(format!(
                "letter used twice on plugboard: {}{}",
                alphabet::to_char(a),
                alphabet::to_char(b)
            )));
        }
        self.map[a as usize] = b;
        self.map[b as usize] = a;
        Ok(())
    }

    #[inline]
    pub fn map(&self, c: u8) -> u8 {
        self.map[c as usize]
    }

    pub fn as_map(&self) -> [u8; N] {
        self.map
    }

    pub fn pairs(&self) -> Vec<(u8, u8)> {
        (0..N as u8).filter(|&a| self.map[a as usize] > a).map(|a| (a, self.map[a as usize])).collect()
    }

    /// Canonical string, pairs sorted: `"AV BS CG"`.
    pub fn to_pairs_string(&self) -> String {
        self.pairs()
            .iter()
            .map(|&(a, b)| format!("{}{}", alphabet::to_char(a), alphabet::to_char(b)))
            .collect::<Vec<_>>()
            .join(" ")
    }
}

/// Full key description, used to build an [`Enigma`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Key {
    pub model: Model,
    pub reflector: String,
    /// Greek wheel (M4 only).
    pub greek: Option<String>,
    /// Left, middle, right.
    pub rotors: [String; 3],
    /// Ring settings, left to right; 4 letters for M4 (greek first), else 3.
    pub rings: Vec<u8>,
    /// Window positions at the start of the message; same layout as `rings`.
    pub positions: Vec<u8>,
    pub plugboard: Plugboard,
}

impl Key {
    /// Convenience constructor from strings: rings/positions as letters ("BUL", "AAAA").
    pub fn parse(
        model: &str,
        reflector: &str,
        greek: Option<&str>,
        rotors: [&str; 3],
        rings: &str,
        positions: &str,
        plugboard: &str,
    ) -> Result<Key> {
        Ok(Key {
            model: Model::parse(model)?,
            reflector: reflector.to_string(),
            greek: greek.map(|g| g.to_string()),
            rotors: rotors.map(|r| r.to_string()),
            rings: alphabet::parse_letters(rings)?,
            positions: alphabet::parse_letters(positions)?,
            plugboard: Plugboard::from_pairs(plugboard)?,
        })
    }
}

/// An Enigma machine with mutable rotor state.
#[derive(Debug, Clone)]
pub struct Enigma {
    pub model: Model,
    pub reflector: &'static ReflectorSpec,
    pub greek: Option<Rotor>,
    /// Left, middle, right.
    pub rotors: [Rotor; 3],
    pub plugboard: Plugboard,
}

impl Enigma {
    pub fn new(key: &Key) -> Result<Enigma> {
        let model = key.model;
        let reflector = wiring::reflector(&key.reflector)?;
        if !model.reflector_names().contains(&reflector.name.as_str()) {
            return Err(Error::Config(format!("reflector {} not available on {}", reflector.name, model.name())));
        }
        let n = if model.has_greek() { 4 } else { 3 };
        if key.rings.len() != n || key.positions.len() != n {
            return Err(Error::Config(format!("{} needs {n} ring settings and {n} positions", model.name())));
        }
        let greek = match (model.has_greek(), &key.greek) {
            (true, Some(g)) => {
                let spec = wiring::rotor(g)?;
                if !spec.greek {
                    return Err(Error::Config(format!("{} is not a greek wheel", spec.name)));
                }
                Some(Rotor::new(spec, key.rings[0], key.positions[0]))
            }
            (true, None) => return Err(Error::Config("M4 needs a greek wheel (BETA or GAMMA)".into())),
            (false, Some(_)) => return Err(Error::Config(format!("{} has no greek wheel", model.name()))),
            (false, None) => None,
        };
        let o = n - 3;
        let mut rotors = Vec::with_capacity(3);
        for (i, name) in key.rotors.iter().enumerate() {
            let spec = wiring::rotor(name)?;
            if spec.greek || !model.rotor_names().contains(&spec.name.as_str()) {
                return Err(Error::Config(format!("rotor {} not available on {}", spec.name, model.name())));
            }
            if key.rotors[..i].iter().any(|r| r.trim().eq_ignore_ascii_case(name.trim())) {
                return Err(Error::Config(format!("rotor {} used twice", spec.name)));
            }
            rotors.push(Rotor::new(spec, key.rings[o + i], key.positions[o + i]));
        }
        let rotors: [Rotor; 3] = rotors.try_into().expect("three rotors");
        Ok(Enigma { model, reflector, greek, rotors, plugboard: key.plugboard })
    }

    /// Advance the rotors as one key press does, including the middle-rotor double step.
    pub fn step(&mut self) {
        let right_turn = self.rotors[2].at_turnover();
        let middle_turn = self.rotors[1].at_turnover();
        if middle_turn {
            self.rotors[1].advance();
            self.rotors[0].advance();
        } else if right_turn {
            self.rotors[1].advance();
        }
        self.rotors[2].advance();
    }

    /// The rotor/reflector scrambler at the current position, without plugboard.
    #[inline]
    pub fn scramble(&self, c: u8) -> u8 {
        let [l, m, r] = &self.rotors;
        let mut x = r.forward(c);
        x = m.forward(x);
        x = l.forward(x);
        if let Some(g) = &self.greek {
            x = g.forward(x);
        }
        x = self.reflector.map[x as usize];
        if let Some(g) = &self.greek {
            x = g.backward(x);
        }
        x = l.backward(x);
        x = m.backward(x);
        r.backward(x)
    }

    /// One key press: step, then encipher.
    pub fn press(&mut self, c: u8) -> u8 {
        self.step();
        self.plugboard.map(self.scramble(self.plugboard.map(c)))
    }

    pub fn process(&mut self, text: &[u8]) -> Vec<u8> {
        text.iter().map(|&c| self.press(c)).collect()
    }

    /// Encipher/decipher a string; non-letters are dropped.
    pub fn process_str(&mut self, text: &str) -> String {
        alphabet::to_string(&self.process(&alphabet::clean(text)))
    }

    /// Window letters (greek first on M4).
    pub fn positions(&self) -> Vec<u8> {
        let mut v = Vec::with_capacity(4);
        if let Some(g) = &self.greek {
            v.push(g.pos);
        }
        v.extend(self.rotors.iter().map(|r| r.pos));
        v
    }

    pub fn set_positions(&mut self, positions: &[u8]) -> Result<()> {
        let n = if self.greek.is_some() { 4 } else { 3 };
        if positions.len() != n {
            return Err(Error::Config(format!("expected {n} positions")));
        }
        let o = n - 3;
        if let Some(g) = &mut self.greek {
            g.pos = positions[0] % 26;
        }
        for i in 0..3 {
            self.rotors[i].pos = positions[o + i] % 26;
        }
        Ok(())
    }

    pub fn window(&self) -> String {
        alphabet::to_string(&self.positions())
    }

    /// Core offsets (window − ring) — what the bombe actually searches. Greek first on M4.
    pub fn cores(&self) -> Vec<u8> {
        let mut v = Vec::with_capacity(4);
        if let Some(g) = &self.greek {
            v.push(g.core());
        }
        v.extend(self.rotors.iter().map(|r| r.core()));
        v
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn machine(model: &str, refl: &str, greek: Option<&str>, rotors: [&str; 3], rings: &str, pos: &str, pb: &str) -> Enigma {
        Enigma::new(&Key::parse(model, refl, greek, rotors, rings, pos, pb).unwrap()).unwrap()
    }

    #[test]
    fn classic_aaaaa() {
        let mut e = machine("enigma-i", "B", None, ["I", "II", "III"], "AAA", "AAA", "");
        assert_eq!(e.process_str("AAAAA"), "BDZGO");
    }

    #[test]
    fn reciprocal_and_never_self() {
        let key = Key::parse("m3", "C", None, ["VI", "II", "VIII"], "KQZ", "MEP", "AB CD EF GH").unwrap();
        let plain = alphabet::clean("DIESISTEINLANGERTESTTEXTFUERDIEENIGMAMASCHINE");
        let cipher = Enigma::new(&key).unwrap().process(&plain);
        assert!(plain.iter().zip(&cipher).all(|(p, c)| p != c));
        assert_eq!(Enigma::new(&key).unwrap().process(&cipher), plain);
    }

    #[test]
    fn double_step() {
        // Rotors I-II-III at ADU: ADV, AEW (middle double-steps next), BFX, BFY.
        let mut e = machine("enigma-i", "B", None, ["I", "II", "III"], "AAA", "ADU", "");
        let mut seen = Vec::new();
        for _ in 0..4 {
            e.step();
            seen.push(e.window());
        }
        assert_eq!(seen, ["ADV", "AEW", "BFX", "BFY"]);
    }

    #[test]
    fn two_notch_rotor() {
        // Rotor VI turns over at Z and M.
        let mut e = machine("m3", "B", None, ["I", "II", "VI"], "AAA", "AAL", "");
        e.step(); // L -> M
        e.step(); // M -> N, middle steps
        assert_eq!(e.window(), "ABN");
        e.set_positions(&alphabet::parse_letters("ABY").unwrap()).unwrap();
        e.step();
        e.step(); // Z -> A, middle steps
        assert_eq!(e.window(), "ACA");
    }

    #[test]
    fn m4_thin_reflector_equivalence() {
        // M4 with greek wheel at A (ring A) and thin reflector equals M3 with the thick reflector.
        let plain = alphabet::clean("VONVONJLOOKSJHFOEUNFTSEEHSXNEUNXSIEBENGRADSUED");
        for (thin, greek, thick) in [("B-THIN", "BETA", "B"), ("C-THIN", "GAMMA", "C")] {
            let mut m4 = machine("m4", thin, Some(greek), ["II", "IV", "I"], "AAVZ", "AVQX", "AT BL DF GJ HM NW OP QY RZ VX");
            let mut m3 = machine("m3", thick, None, ["II", "IV", "I"], "AVZ", "VQX", "AT BL DF GJ HM NW OP QY RZ VX");
            assert_eq!(m4.process(&plain), m3.process(&plain), "{thin}+{greek} vs {thick}");
        }
    }

    #[test]
    fn rejects_bad_keys() {
        assert!(Key::parse("enigma-i", "B", None, ["I", "II", "VI"], "AAA", "AAA", "").and_then(|k| Enigma::new(&k)).is_err());
        assert!(Key::parse("enigma-i", "B", None, ["I", "I", "II"], "AAA", "AAA", "").and_then(|k| Enigma::new(&k)).is_err());
        assert!(Key::parse("m4", "B", Some("BETA"), ["I", "II", "III"], "AAAA", "AAAA", "").and_then(|k| Enigma::new(&k)).is_err());
        assert!(Plugboard::from_pairs("AB AC").is_err());
    }
}
