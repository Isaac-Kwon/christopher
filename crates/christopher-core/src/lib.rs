//! Project Christopher — reference model of the Enigma machine and the Turing–Welchman Bombe.
//!
//! The crate is the "golden model" for later RTL and physical builds, so the structure follows the
//! real machines rather than being an abstract search:
//!
//! * [`enigma`]  — Enigma I / M3 / M4 (rotors, reflectors, greek wheels, plugboard, double stepping).
//! * [`menu`]    — aligning a crib against ciphertext and building the bombe menu.
//! * [`bombe`]   — scramblers (drum sets), 26-wire cables, diagonal board, test register, stops.
//! * [`check`]   — the checking stage: from a stop to a full key and plaintext.
//! * [`search`]  — runs the bombe over many wheel orders (in parallel) and checks every stop.
//!
//! Letters are represented as `u8` in `0..26` (A = 0). Wiring tables live in
//! `data/wirings/enigma.txt` and are compiled in.

pub mod alphabet;
pub mod bombe;
pub mod check;
pub mod enigma;
pub mod error;
pub mod menu;
pub mod relay;
pub mod search;
pub mod wiring;

pub use error::{Error, Result};
