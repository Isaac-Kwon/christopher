//! Cribs and menus.
//!
//! A crib is a guessed piece of plaintext placed at some offset in the ciphertext. Each crib letter
//! gives an edge `plain —k— cipher`: at crib position `k` the Enigma maps the steckered plain letter
//! to the steckered cipher letter. The set of edges is the *menu*, wired into the bombe with one
//! scrambler per edge.

use crate::alphabet::{self, N};
use crate::error::{Error, Result};

/// One menu connection: at crib position `pos`, `plain` and `cipher` are joined by a scrambler.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Edge {
    pub plain: u8,
    pub cipher: u8,
    /// Position within the crib (0-based); the scrambler for this edge sits `pos` steps after the
    /// first crib letter.
    pub pos: u16,
}

/// A bombe menu: edges plus the chosen test letter and the wire energised at the test register.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Menu {
    /// Index of the first crib letter in the ciphertext.
    pub offset: usize,
    /// The crib that produced this menu.
    pub crib: Vec<u8>,
    pub edges: Vec<Edge>,
    /// Letter whose cable carries the test register (usually the most connected letter).
    pub test_letter: u8,
    /// Wire of the test cable that receives the input current (hypothesis `S(test_letter) = input_wire`).
    pub input_wire: u8,
}

/// Offsets at which `crib` can sit against `ciphertext`: Enigma never enciphers a letter to itself,
/// so any offset where a crib letter equals the cipher letter beneath it is impossible.
pub fn possible_offsets(ciphertext: &[u8], crib: &[u8]) -> Vec<usize> {
    if crib.is_empty() || crib.len() > ciphertext.len() {
        return Vec::new();
    }
    (0..=ciphertext.len() - crib.len())
        .filter(|&o| crib.iter().zip(&ciphertext[o..]).all(|(p, c)| p != c))
        .collect()
}

impl Menu {
    /// Build the full menu (every crib letter becomes an edge).
    pub fn from_crib(ciphertext: &[u8], crib: &[u8], offset: usize) -> Result<Menu> {
        if crib.is_empty() {
            return Err(Error::Config("empty crib".into()));
        }
        if offset + crib.len() > ciphertext.len() {
            return Err(Error::Config("crib runs past the end of the ciphertext".into()));
        }
        let mut edges = Vec::with_capacity(crib.len());
        for (k, (&p, &c)) in crib.iter().zip(&ciphertext[offset..]).enumerate() {
            if p == c {
                return Err(Error::Config(format!(
                    "crib impossible at offset {offset}: {} would encipher to itself at crib position {k}",
                    alphabet::to_char(p)
                )));
            }
            edges.push(Edge { plain: p, cipher: c, pos: k as u16 });
        }
        let mut menu = Menu { offset, crib: crib.to_vec(), edges, test_letter: 0, input_wire: 0 };
        menu.choose_test_letter();
        Ok(menu)
    }

    /// Sub-menu of the edges at the given crib positions (0-based), keeping the crib and offset so
    /// the result can still be checked. The test letter is chosen again for the sub-menu.
    pub fn subset(&self, positions: &[u16]) -> Result<Menu> {
        let edges: Vec<Edge> = self.edges.iter().copied().filter(|e| positions.contains(&e.pos)).collect();
        if let Some(&p) = positions.iter().find(|&&p| !self.edges.iter().any(|e| e.pos == p)) {
            return Err(Error::Config(format!("crib position {p} is not in the menu")));
        }
        if edges.is_empty() {
            return Err(Error::Config("empty sub-menu".into()));
        }
        let mut menu = Menu { offset: self.offset, crib: self.crib.clone(), edges, test_letter: 0, input_wire: 0 };
        menu.choose_test_letter();
        Ok(menu)
    }

    /// Build a menu from explicit edges (for hand-made menus).
    pub fn from_edges(edges: Vec<Edge>, test_letter: u8, input_wire: u8) -> Result<Menu> {
        if edges.iter().any(|e| e.plain == e.cipher || e.plain as usize >= N || e.cipher as usize >= N) {
            return Err(Error::Config("invalid edge".into()));
        }
        let crib_len = edges.iter().map(|e| e.pos as usize + 1).max().unwrap_or(0);
        Ok(Menu { offset: 0, crib: vec![0; crib_len], edges, test_letter, input_wire })
    }

    /// Degree of each letter in the menu graph.
    pub fn degrees(&self) -> [usize; N] {
        let mut d = [0usize; N];
        for e in &self.edges {
            d[e.plain as usize] += 1;
            d[e.cipher as usize] += 1;
        }
        d
    }

    /// Bitmask of letters appearing in the menu.
    pub fn letters(&self) -> u32 {
        self.edges.iter().fold(0, |m, e| m | 1 << e.plain | 1 << e.cipher)
    }

    /// Connected components as letter bitmasks, largest first.
    pub fn components(&self) -> Vec<u32> {
        let mut parent: [u8; N] = std::array::from_fn(|i| i as u8);
        fn find(p: &mut [u8; N], x: u8) -> u8 {
            let mut r = x;
            while p[r as usize] != r {
                r = p[r as usize];
            }
            let mut y = x;
            while p[y as usize] != r {
                let next = p[y as usize];
                p[y as usize] = r;
                y = next;
            }
            r
        }
        for e in &self.edges {
            let (a, b) = (find(&mut parent, e.plain), find(&mut parent, e.cipher));
            if a != b {
                parent[a as usize] = b;
            }
        }
        let letters = self.letters();
        let mut comps = [0u32; N];
        for l in 0..N as u8 {
            if letters >> l & 1 == 1 {
                let r = find(&mut parent, l);
                comps[r as usize] |= 1 << l;
            }
        }
        let mut out: Vec<u32> = comps.into_iter().filter(|&m| m != 0).collect();
        out.sort_by_key(|m| std::cmp::Reverse(m.count_ones()));
        out
    }

    /// Number of independent closures (loops): edges − letters + components.
    pub fn loops(&self) -> usize {
        let letters = self.letters().count_ones() as usize;
        (self.edges.len() + self.components().len()).saturating_sub(letters)
    }

    /// Span of crib positions used (max pos − min pos + 1). Shorter spans are less likely to
    /// contain a middle-rotor turnover.
    pub fn span(&self) -> usize {
        match (self.edges.iter().map(|e| e.pos).min(), self.edges.iter().map(|e| e.pos).max()) {
            (Some(a), Some(b)) => (b - a) as usize + 1,
            _ => 0,
        }
    }

    fn choose_test_letter(&mut self) {
        let deg = self.degrees();
        let comp = self.components().first().copied().unwrap_or(0);
        let best = (0..N as u8).filter(|&l| comp >> l & 1 == 1).max_by_key(|&l| (deg[l as usize], std::cmp::Reverse(l)));
        self.test_letter = best.unwrap_or(0);
        // Any wire works as the input; avoid the test letter itself (a letter may be self-steckered,
        // but choosing a different wire keeps the display convention "25 live = stecker is the dark one").
        self.input_wire = if self.test_letter == 0 { 1 } else { 0 };
    }

    /// Choose a loop-rich connected sub-menu with at most `max_edges` edges (one scrambler each).
    ///
    /// Greedy, in the spirit of hand menu-making: start from the most connected letter, prefer
    /// edges that close a loop, otherwise extend to the best-connected new letter.
    pub fn select(&self, max_edges: usize) -> Menu {
        let comp = self.components().first().copied().unwrap_or(0);
        let mut remaining: Vec<Edge> =
            self.edges.iter().copied().filter(|e| comp >> e.plain & 1 == 1).collect();
        let deg = self.degrees();
        let start = (0..N as u8)
            .filter(|&l| comp >> l & 1 == 1)
            .max_by_key(|&l| (deg[l as usize], std::cmp::Reverse(l)))
            .unwrap_or(0);
        let mut have: u32 = 1 << start;
        let mut chosen: Vec<Edge> = Vec::new();
        while chosen.len() < max_edges && !remaining.is_empty() {
            let inside = |e: &Edge| have >> e.plain & 1 == 1 && have >> e.cipher & 1 == 1;
            let touches = |e: &Edge| have >> e.plain & 1 == 1 || have >> e.cipher & 1 == 1;
            let pick = if let Some(i) = remaining.iter().position(inside) {
                Some(i)
            } else {
                remaining
                    .iter()
                    .enumerate()
                    .filter(|(_, e)| touches(e))
                    .max_by_key(|(_, e)| {
                        let new = if have >> e.plain & 1 == 1 { e.cipher } else { e.plain };
                        (deg[new as usize], std::cmp::Reverse(e.pos))
                    })
                    .map(|(i, _)| i)
            };
            match pick {
                Some(i) => {
                    let e = remaining.swap_remove(i);
                    have |= 1 << e.plain | 1 << e.cipher;
                    chosen.push(e);
                }
                None => break,
            }
        }
        chosen.sort_by_key(|e| e.pos);
        let mut menu = Menu { offset: self.offset, crib: self.crib.clone(), edges: chosen, test_letter: 0, input_wire: 0 };
        menu.choose_test_letter();
        menu
    }

    /// Human-readable description (edges, loops, test letter).
    pub fn describe(&self) -> String {
        let mut s = format!(
            "menu at offset {}: {} edges, {} letters, {} loops, span {}, test letter {} (input wire {})\n",
            self.offset,
            self.edges.len(),
            self.letters().count_ones(),
            self.loops(),
            self.span(),
            alphabet::to_char(self.test_letter),
            alphabet::to_char(self.input_wire),
        );
        for e in &self.edges {
            s.push_str(&format!("  {:>3}: {} - {}\n", e.pos + 1, alphabet::to_char(e.plain), alphabet::to_char(e.cipher)));
        }
        s
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::alphabet::clean;

    #[test]
    fn offsets_exclude_self_encipherment() {
        let c = clean("ABCDEF");
        // Offset 1 puts crib B over cipher B: impossible.
        assert_eq!(possible_offsets(&c, &clean("BA")), vec![0, 2, 3, 4]);
    }

    #[test]
    fn loops_and_selection() {
        // Triangle A-B, B-C, C-A plus a tail C-D: one loop.
        let edges = vec![
            Edge { plain: 0, cipher: 1, pos: 0 },
            Edge { plain: 1, cipher: 2, pos: 1 },
            Edge { plain: 2, cipher: 0, pos: 2 },
            Edge { plain: 2, cipher: 3, pos: 3 },
        ];
        let m = Menu::from_edges(edges, 2, 0).unwrap();
        assert_eq!(m.loops(), 1);
        assert_eq!(m.components().len(), 1);
        assert_eq!(m.test_letter, 2); // C has the highest degree in the full menu
        let s = m.select(3);
        assert_eq!(s.edges.len(), 3);
        assert_eq!(s.loops(), 1); // selection keeps the triangle, drops the tail
        assert_eq!(s.letters(), 0b111);
    }
}
