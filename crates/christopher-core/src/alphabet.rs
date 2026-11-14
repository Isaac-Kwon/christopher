//! Letter <-> index conversion. A = 0 … Z = 25.

use crate::error::{Error, Result};

pub const N: usize = 26;
/// Bitmask with all 26 wires set.
pub const FULL: u32 = (1 << N) - 1;

#[inline]
pub fn to_index(c: char) -> Option<u8> {
    let u = c.to_ascii_uppercase();
    if u.is_ascii_uppercase() {
        Some(u as u8 - b'A')
    } else {
        None
    }
}

#[inline]
pub fn to_char(i: u8) -> char {
    (b'A' + i) as char
}

/// Keep only the letters of `text` (case-insensitive); everything else is dropped.
pub fn clean(text: &str) -> Vec<u8> {
    text.chars().filter_map(to_index).collect()
}

/// Parse letters strictly: whitespace is ignored, any other non-letter is an error.
pub fn parse_letters(text: &str) -> Result<Vec<u8>> {
    let mut out = Vec::with_capacity(text.len());
    for c in text.chars() {
        if c.is_whitespace() {
            continue;
        }
        match to_index(c) {
            Some(i) => out.push(i),
            None => return Err(Error::Parse(format!("not a letter: {c:?} in {text:?}"))),
        }
    }
    Ok(out)
}

pub fn to_string(letters: &[u8]) -> String {
    letters.iter().map(|&i| to_char(i)).collect()
}

/// Letters set in a 26-bit mask, as a string ("ACZ").
pub fn mask_to_string(mask: u32) -> String {
    (0..N as u8).filter(|&i| mask >> i & 1 == 1).map(to_char).collect()
}

#[inline]
pub fn add(a: u8, b: u8) -> u8 {
    ((a as usize + b as usize) % N) as u8
}

#[inline]
pub fn sub(a: u8, b: u8) -> u8 {
    ((a as usize + N - b as usize % N) % N) as u8
}
