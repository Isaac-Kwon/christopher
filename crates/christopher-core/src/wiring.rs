//! Wiring tables, parsed once from `data/wirings/enigma.txt`.

use std::sync::OnceLock;

use crate::alphabet::{self, N};
use crate::error::{Error, Result};

const SOURCE: &str = include_str!("../../../data/wirings/enigma.txt");

/// A rotor or greek wheel.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RotorSpec {
    pub name: String,
    /// `forward[c]` = output on the reflector side for input `c` on the entry side, core offset 0.
    pub forward: [u8; N],
    /// Inverse of `forward`.
    pub backward: [u8; N],
    /// Window letters at which this rotor carries to its left neighbour (empty for greek wheels).
    pub turnovers: Vec<u8>,
    pub greek: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReflectorSpec {
    pub name: String,
    pub map: [u8; N],
}

#[derive(Debug)]
pub struct Tables {
    pub rotors: Vec<RotorSpec>,
    pub reflectors: Vec<ReflectorSpec>,
}

fn parse_perm(name: &str, s: &str) -> Result<[u8; N]> {
    let letters = alphabet::parse_letters(s)?;
    if letters.len() != N {
        return Err(Error::Parse(format!("{name}: wiring must have 26 letters")));
    }
    let mut seen = 0u32;
    let mut out = [0u8; N];
    for (i, &l) in letters.iter().enumerate() {
        seen |= 1 << l;
        out[i] = l;
    }
    if seen != alphabet::FULL {
        return Err(Error::Parse(format!("{name}: wiring is not a permutation")));
    }
    Ok(out)
}

fn parse(source: &str) -> Result<Tables> {
    let mut rotors = Vec::new();
    let mut reflectors = Vec::new();
    for raw in source.lines() {
        let line = raw.split('#').next().unwrap_or("").trim();
        if line.is_empty() {
            continue;
        }
        let cols: Vec<&str> = line.split_whitespace().collect();
        if cols.len() != 4 {
            return Err(Error::Parse(format!("wiring line needs 4 columns: {raw:?}")));
        }
        let (kind, name, wiring, turnover) = (cols[0], cols[1].to_ascii_uppercase(), cols[2], cols[3]);
        let forward = parse_perm(&name, wiring)?;
        match kind {
            "rotor" | "greek" => {
                let mut backward = [0u8; N];
                for (i, &o) in forward.iter().enumerate() {
                    backward[o as usize] = i as u8;
                }
                let turnovers = if turnover == "-" { Vec::new() } else { alphabet::parse_letters(turnover)? };
                rotors.push(RotorSpec { name, forward, backward, turnovers, greek: kind == "greek" });
            }
            "reflector" => {
                for (i, &o) in forward.iter().enumerate() {
                    if o as usize == i || forward[o as usize] as usize != i {
                        return Err(Error::Parse(format!("{name}: reflector must be a fixed-point-free involution")));
                    }
                }
                reflectors.push(ReflectorSpec { name, map: forward });
            }
            other => return Err(Error::Parse(format!("unknown wiring kind {other:?}"))),
        }
    }
    Ok(Tables { rotors, reflectors })
}

pub fn tables() -> &'static Tables {
    static TABLES: OnceLock<Tables> = OnceLock::new();
    TABLES.get_or_init(|| parse(SOURCE).expect("data/wirings/enigma.txt is invalid"))
}

/// Look up a rotor or greek wheel by name (case-insensitive): "I".."VIII", "BETA", "GAMMA".
pub fn rotor(name: &str) -> Result<&'static RotorSpec> {
    let key = name.trim().to_ascii_uppercase();
    tables()
        .rotors
        .iter()
        .find(|r| r.name == key)
        .ok_or_else(|| Error::Config(format!("unknown rotor {name:?}")))
}

/// Look up a reflector by name (case-insensitive): "B", "C", "B-THIN", "C-THIN".
/// "B_THIN", "BTHIN", "B THIN" are accepted as aliases.
pub fn reflector(name: &str) -> Result<&'static ReflectorSpec> {
    let mut key = name.trim().to_ascii_uppercase().replace(['_', ' '], "-");
    if key.ends_with("THIN") && !key.contains('-') {
        key.insert(key.len() - 4, '-');
    }
    tables()
        .reflectors
        .iter()
        .find(|r| r.name == key)
        .ok_or_else(|| Error::Config(format!("unknown reflector {name:?}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table_is_complete() {
        let t = tables();
        assert_eq!(t.rotors.len(), 10);
        assert_eq!(t.reflectors.len(), 4);
        assert_eq!(rotor("vi").unwrap().turnovers, vec![25, 12]);
        assert!(rotor("beta").unwrap().greek);
        assert_eq!(reflector("b_thin").unwrap().name, "B-THIN");
        assert_eq!(reflector("CTHIN").unwrap().name, "C-THIN");
        assert!(rotor("IX").is_err());
    }
}
