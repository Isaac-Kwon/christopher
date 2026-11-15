//! False-stop statistics vs. menu shape (letters, loops), to compare with Turing's estimates.
//!
//!     cargo run --release -p christopher-core --example stop_statistics [trials]
//!
//! Each trial builds a random connected menu with the requested number of letters and loops
//! (a random spanning tree plus extra edges, distinct crib positions 0..25, random letters) and
//! runs it on a random Enigma I wheel order. Scramblers then behave like the random permutations
//! a wrong key gives, so every stop counted is a false stop. Output: mean stops per wheel order
//! (17,576 positions), with and without the diagonal board. Prints a Markdown table.

use christopher_core::bombe::{run, BombeOptions, WheelOrder};
use christopher_core::enigma::Model;
use christopher_core::menu::{Edge, Menu};
use rayon::prelude::*;

/// xorshift64* — deterministic, dependency-free.
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    fn below(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }
    fn shuffle<T>(&mut self, v: &mut [T]) {
        for i in (1..v.len()).rev() {
            v.swap(i, self.below(i + 1));
        }
    }
}

fn random_menu(rng: &mut Rng, letters: usize, loops: usize) -> Menu {
    let edges_n = letters - 1 + loops;
    let mut alphabet: Vec<u8> = (0..26).collect();
    rng.shuffle(&mut alphabet);
    let ls = &alphabet[..letters];
    let mut positions: Vec<u16> = (0..26).collect();
    rng.shuffle(&mut positions);
    let mut pairs: Vec<(u8, u8)> = Vec::new();
    // Spanning tree: attach each new letter to a random earlier one.
    for i in 1..letters {
        pairs.push((ls[i], ls[rng.below(i)]));
    }
    // Extra edges close loops (a repeated pair is still a loop through two scramblers).
    for _ in 0..loops {
        let a = rng.below(letters);
        let mut b = rng.below(letters);
        while b == a {
            b = rng.below(letters);
        }
        pairs.push((ls[a], ls[b]));
    }
    let edges: Vec<Edge> =
        pairs.iter().zip(&positions[..edges_n]).map(|(&(p, c), &pos)| Edge { plain: p, cipher: c, pos }).collect();
    let mut m = Menu::from_edges(edges, 0, 0).expect("valid menu");
    // Test letter: the most connected one, as an operator would choose.
    let deg = m.degrees();
    m.test_letter = (0..26u8).max_by_key(|&l| deg[l as usize]).unwrap();
    m.input_wire = if m.test_letter == 0 { 1 } else { 0 };
    m
}

fn main() {
    let trials: usize = std::env::args().nth(1).and_then(|s| s.parse().ok()).unwrap_or(40);
    let orders = WheelOrder::all(Model::EnigmaI);
    let cases: Vec<(usize, usize)> = [8, 10, 12, 14].iter().flat_map(|&l| (0..=3).map(move |c| (l, c))).collect();
    println!("trials per case: {trials} (random Enigma I wheel order each)\n");
    println!("| letters | loops | edges | stops/order (diagonal board) | stops/order (no diagonal board) |");
    println!("|---:|---:|---:|---:|---:|");
    for (letters, loops) in cases {
        let (with, without): (Vec<usize>, Vec<usize>) = (0..trials)
            .into_par_iter()
            .map(|t| {
                let mut rng = Rng(0x9E37_79B9_7F4A_7C15 ^ ((letters * 1000 + loops * 100 + t) as u64).wrapping_mul(0xD1B5_4A32_D192_ED03));
                let menu = random_menu(&mut rng, letters, loops);
                let order = &orders[rng.below(orders.len())];
                let w = run(&menu, order, &BombeOptions { diagonal_board: true, turnover_at: None, fixed_right: None }).stops.len();
                let wo = run(&menu, order, &BombeOptions { diagonal_board: false, turnover_at: None, fixed_right: None }).stops.len();
                (w, wo)
            })
            .unzip();
        let mean = |v: &[usize]| v.iter().sum::<usize>() as f64 / v.len() as f64;
        println!(
            "| {letters} | {loops} | {} | {:.2} | {:.1} |",
            letters - 1 + loops,
            mean(&with),
            mean(&without)
        );
    }
}
