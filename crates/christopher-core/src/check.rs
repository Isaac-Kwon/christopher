//! The checking stage: from a bombe stop to a full key and plaintext.
//!
//! 1. **Stecker hypotheses.** For each candidate value `v` of S(test letter), spread current from
//!    that single wire. A true hypothesis only implies true facts, so every cable may end with at
//!    most one live wire; a cable with two live wires refutes `v`. Survivors give partial steckers.
//! 2. **Whole crib.** Every crib letter (not only the menu edges) must agree with the scramblers
//!    at the stop; this extends the steckers and rejects most false stops.
//! 3. **Ring settings.** The bombe works on core offsets, so the right and middle ring settings are
//!    still unknown. Each combination fixes when the middle (and left) rotor turn over; we keep
//!    those consistent with the bombe's turnover assumption, wind the rotors back to the start of
//!    the message, decipher, and keep the best-scoring plaintext.
//! 4. **Remaining steckers.** Letters that never occur in the crib are still unknown; a small
//!    hill-climb on German bigram statistics proposes pairs for them (reported as unresolved).

use std::sync::OnceLock;

use crate::alphabet::{self, N};
use crate::bombe::{BombeOptions, Plugged, Position, Stop, WheelOrder};
use crate::enigma::{Enigma, Key, Model, Plugboard, Rotor};
use crate::menu::Menu;

const UNKNOWN: u8 = 255;

/// A key recovered from a stop.
#[derive(Debug, Clone)]
pub struct Solution {
    pub wheel_order: WheelOrder,
    pub stop_position: Position,
    /// Full key: rings, start positions for the whole message, and plugboard.
    ///
    /// Only the right ring (and, when the left rotor steps during the message, the middle ring)
    /// is actually determined; the left and greek rings are reported as A, and an undetermined
    /// middle ring is reported as the first value that deciphers equally well. The positions are
    /// adjusted so the key is equivalent to the true one.
    pub key: Key,
    pub plaintext: Vec<u8>,
    /// Letters whose stecker was not determined by the crib (bitmask).
    pub unresolved: u32,
    /// Subset of `unresolved` that the language-model hill-climb paired up (guessed pairs);
    /// the rest of `unresolved` is left self-steckered.
    pub guessed: u32,
    /// Middle-rotor turnover hypothesis of the stop that produced this solution.
    pub turnover_at: Option<u16>,
    /// Plaintext score ([`score_german`], higher is better).
    pub score: f64,
}

impl Solution {
    pub fn plaintext_string(&self) -> String {
        alphabet::to_string(&self.plaintext)
    }
}

/// German letter-bigram counts (see data/language/README.md for the source corpus).
const BIGRAMS: &str = include_str!("../../../data/language/german_bigrams.txt");

/// `ln P(b | a)` with add-one smoothing.
fn bigram_log_probs() -> &'static [[f64; N]; N] {
    static TABLE: OnceLock<[[f64; N]; N]> = OnceLock::new();
    TABLE.get_or_init(|| {
        let rows: Vec<Vec<f64>> = BIGRAMS
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty() && !l.starts_with('#'))
            .map(|l| l.split_whitespace().map(|x| x.parse::<f64>().expect("bigram count")).collect())
            .collect();
        assert!(rows.len() == N && rows.iter().all(|r| r.len() == N), "german_bigrams.txt must be 26×26");
        std::array::from_fn(|a| {
            let total: f64 = rows[a].iter().sum::<f64>() + N as f64;
            std::array::from_fn(|b| ((rows[a][b] + 1.0) / total).ln())
        })
    })
}

/// How German a text looks: mean `ln P(next letter | letter)` under the bigram model.
/// Random text scores about −3.5, ordinary German prose about −2.4.
pub fn score_german(text: &[u8]) -> f64 {
    if text.len() < 2 {
        return f64::NEG_INFINITY;
    }
    let t = bigram_log_probs();
    text.windows(2).map(|w| t[w[0] as usize][w[1] as usize]).sum::<f64>() / (text.len() - 1) as f64
}

fn set_pair(map: &mut [u8; N], a: u8, b: u8) -> bool {
    let (ma, mb) = (map[a as usize], map[b as usize]);
    if (ma != UNKNOWN && ma != b) || (mb != UNKNOWN && mb != a) {
        return false;
    }
    map[a as usize] = b;
    map[b as usize] = a;
    true
}

/// Extend partial steckers with every crib letter; `None` on contradiction.
fn extend_with_crib(
    mut map: [u8; N],
    order: &WheelOrder,
    p: Position,
    turnover_at: Option<u16>,
    crib: &[u8],
    cipher: &[u8],
) -> Option<[u8; N]> {
    let tables = Plugged::tables(order, p.greek, p.left, p.middle, turnover_at.is_some());
    let scr = |k: usize| -> &[u8; N] {
        let r = (p.right as usize + k) % N;
        match turnover_at {
            Some(t) if k >= t as usize => &tables[N + r],
            _ => &tables[r],
        }
    };
    loop {
        let mut changed = false;
        for k in 0..crib.len() {
            let (pl, ci, s) = (crib[k], cipher[k], scr(k));
            let (sp, sc) = (map[pl as usize], map[ci as usize]);
            if sp != UNKNOWN {
                let want = s[sp as usize];
                if sc == UNKNOWN {
                    if !set_pair(&mut map, ci, want) {
                        return None;
                    }
                    changed = true;
                } else if sc != want {
                    return None;
                }
            } else if sc != UNKNOWN {
                if !set_pair(&mut map, pl, s[sc as usize]) {
                    return None;
                }
                changed = true;
            }
        }
        if !changed {
            return Some(map);
        }
    }
}

/// Stecker hypotheses consistent with the menu at this stop (step 1).
pub fn stecker_hypotheses(menu: &Menu, stop: &Stop, diagonal: bool) -> Vec<[u8; N]> {
    let plugged = Plugged::new(menu);
    let opts = BombeOptions { diagonal_board: diagonal, turnover_at: stop.turnover_at, fixed_right: None };
    let mut out = Vec::new();
    for v in 0..N as u8 {
        let live = plugged.trace(&stop.wheel_order, stop.position, v, &opts);
        if live.iter().any(|m| m.count_ones() > 1) {
            continue;
        }
        let mut map = [UNKNOWN; N];
        let mut ok = true;
        for (a, &m) in live.iter().enumerate() {
            if m != 0 {
                ok &= set_pair(&mut map, a as u8, m.trailing_zeros() as u8);
            }
        }
        if ok {
            out.push(map);
        }
    }
    out
}

fn model_of(order: &WheelOrder) -> Model {
    if order.greek.is_some() {
        Model::M4
    } else if order.rotors.iter().any(|r| !["I", "II", "III", "IV", "V"].contains(&r.name.as_str()))
        || !["B", "C"].contains(&order.reflector.name.as_str())
    {
        Model::M3
    } else {
        Model::EnigmaI
    }
}

fn machine(order: &WheelOrder, rings: [u8; 4], windows: [u8; 4], pb: Plugboard) -> Enigma {
    Enigma {
        model: model_of(order),
        reflector: order.reflector,
        greek: order.greek.map(|g| Rotor::new(g, rings[0], windows[0])),
        rotors: [
            Rotor::new(order.rotors[0], rings[1], windows[1]),
            Rotor::new(order.rotors[1], rings[2], windows[2]),
            Rotor::new(order.rotors[2], rings[3], windows[3]),
        ],
        plugboard: pb,
    }
}

/// Window state at message start that reaches `at_crib` after `presses` key presses, if any.
fn wind_back(order: &WheelOrder, rings: [u8; 4], at_crib: [u8; 4], presses: usize) -> Option<[u8; 4]> {
    let r0 = alphabet::sub(at_crib[3], (presses % N) as u8);
    for dm in 0..=(presses / N + 2).min(N - 1) {
        for dl in 0..=(presses / (N * (N - 1)) + 2).min(N - 1) {
            let start = [at_crib[0], alphabet::sub(at_crib[1], dl as u8), alphabet::sub(at_crib[2], dm as u8), r0];
            let mut e = machine(order, rings, start, Plugboard::identity());
            for _ in 0..presses {
                e.step();
            }
            if e.positions()[e.positions().len() - 3..] == at_crib[1..] {
                return Some(start);
            }
        }
    }
    None
}

fn to_plugboard(map: &[u8; N]) -> Plugboard {
    let full: [u8; N] = std::array::from_fn(|i| if map[i] == UNKNOWN { i as u8 } else { map[i] });
    Plugboard::from_map(full).expect("consistent stecker map")
}

/// Check one stop against the whole crib and recover full keys (usually zero or one).
pub fn check_stop(ciphertext: &[u8], menu: &Menu, stop: &Stop, diagonal: bool) -> Vec<Solution> {
    let order = &stop.wheel_order;
    let p = stop.position;
    let crib = &menu.crib;
    let off = menu.offset;
    if off + crib.len() > ciphertext.len() {
        return Vec::new();
    }
    let cipher_at_crib = &ciphertext[off..off + crib.len()];
    let mut solutions: Vec<Solution> = Vec::new();

    for partial in stecker_hypotheses(menu, stop, diagonal) {
        let Some(map) = extend_with_crib(partial, order, p, stop.turnover_at, crib, cipher_at_crib) else {
            continue;
        };
        // (score, rings, start windows) of the best ring setting.
        let mut best: Option<(f64, [u8; 4], [u8; 4])> = None;
        for ring_r in 0..N as u8 {
            for ring_m in 0..N as u8 {
                let rings = [0, 0, ring_m, ring_r];
                let at_crib = [p.greek, p.left, alphabet::add(p.middle, ring_m), alphabet::add(p.right, ring_r)];
                // Rotor movement during the crib must match the bombe's assumption.
                let mut e = machine(order, rings, at_crib, Plugboard::identity());
                let mut ok = true;
                for k in 1..crib.len() {
                    e.step();
                    let c = e.cores();
                    let mid = match stop.turnover_at {
                        Some(t) if k >= t as usize => alphabet::add(p.middle, 1),
                        _ => p.middle,
                    };
                    let n = c.len();
                    if c[n - 3] != p.left || c[n - 2] != mid || c[n - 1] != alphabet::add(p.right, (k % N) as u8) {
                        ok = false;
                        break;
                    }
                }
                if !ok {
                    continue;
                }
                let Some(start) = wind_back(order, rings, at_crib, off + 1) else { continue };
                let mut e = machine(order, rings, start, to_plugboard(&map));
                let plain = e.process(ciphertext);
                if plain[off..off + crib.len()] != crib[..] {
                    continue;
                }
                let s = score_german(&plain);
                if best.as_ref().is_none_or(|b| s > b.0 + 1e-12) {
                    best = Some((s, rings, start));
                }
            }
        }
        let Some((_, rings, start)) = best else { continue };

        // Remaining steckers: hill-climb on letters the crib never determined.
        let unresolved: u32 = (0..N as u8).filter(|&i| map[i as usize] == UNKNOWN).fold(0, |m, i| m | 1 << i);
        let mut pb = to_plugboard(&map);
        let decipher = |pb: Plugboard| machine(order, rings, start, pb).process(ciphertext);
        let mut plain = decipher(pb);
        let mut score = score_german(&plain);
        loop {
            let mut improved: Option<(f64, Plugboard, Vec<u8>)> = None;
            if pb.pairs().len() >= 13 {
                break;
            }
            for a in 0..N as u8 {
                for b in a + 1..N as u8 {
                    if unresolved >> a & 1 == 0 || unresolved >> b & 1 == 0 || pb.map(a) != a || pb.map(b) != b {
                        continue;
                    }
                    let mut trial = pb;
                    trial.connect(a, b).expect("free letters");
                    let pt = decipher(trial);
                    let s = score_german(&pt);
                    // Require a clear gain so noise does not add spurious pairs.
                    if s > score + 0.01 && improved.as_ref().is_none_or(|x| s > x.0) {
                        improved = Some((s, trial, pt));
                    }
                }
            }
            match improved {
                Some((s, t, pt)) => {
                    score = s;
                    pb = t;
                    plain = pt;
                }
                None => break,
            }
        }

        let model = model_of(order);
        let (rings_v, start_v) = if order.greek.is_some() { (rings.to_vec(), start.to_vec()) } else { (rings[1..].to_vec(), start[1..].to_vec()) };
        let key = Key {
            model,
            reflector: order.reflector.name.clone(),
            greek: order.greek.map(|g| g.name.clone()),
            rotors: order.rotors.map(|r| r.name.clone()),
            rings: rings_v,
            positions: start_v,
            plugboard: pb,
        };
        let guessed = (0..N as u8).filter(|&l| unresolved >> l & 1 == 1 && pb.map(l) != l).fold(0, |m, l| m | 1 << l);
        if !solutions.iter().any(|s| s.plaintext == plain) {
            solutions.push(Solution {
                wheel_order: *order,
                stop_position: p,
                key,
                plaintext: plain,
                unresolved,
                guessed,
                turnover_at: stop.turnover_at,
                score,
            });
        }
    }
    solutions
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::alphabet::clean;
    use crate::bombe::run;

    #[test]
    fn recovers_key_and_plaintext() {
        let key = Key::parse(
            "enigma-i",
            "B",
            None,
            ["IV", "I", "V"],
            "AKH",
            "QWE",
            "AQ BZ CR DM EV FW GK HN IU JP",
        )
        .unwrap();
        let plain = clean(
            "ANXOBERKOMMANDODERWEHRMACHTXWETTERVORHERSAGEFUERDIENORDSEEXHEUTEKLARXWINDAUSWESTXSTAERKEDREI",
        );
        let cipher = Enigma::new(&key).unwrap().process(&plain);
        let crib_text = clean("WETTERVORHERSAGE");
        let off = 28;
        assert_eq!(&plain[off..off + 16], &crib_text[..]);
        let menu = Menu::from_crib(&cipher, &crib_text, off).unwrap();
        let order = WheelOrder::new("B", None, ["IV", "I", "V"]).unwrap();
        let res = run(&menu, &order, &BombeOptions::default());
        let sols: Vec<Solution> = res.stops.iter().flat_map(|s| check_stop(&cipher, &menu, s, true)).collect();
        assert!(!sols.is_empty(), "no solution among {} stops", res.stops.len());
        let best = sols.iter().max_by(|a, b| a.score.total_cmp(&b.score)).unwrap();
        // Rotor order and core offsets are those of the true key.
        assert_eq!(best.key.rotors, key.rotors);
        // Every stecker the crib determined is correct.
        for l in 0..26u8 {
            if best.unresolved >> l & 1 == 0 {
                assert_eq!(best.key.plugboard.map(l), key.plugboard.map(l), "stecker of {}", alphabet::to_char(l));
            }
        }
        // Wherever both the cipher letter and the plain letter have resolved steckers, the
        // plaintext is exact.
        let resolved = |l: u8| best.unresolved >> l & 1 == 0;
        for i in 0..plain.len() {
            if resolved(cipher[i]) && resolved(plain[i]) {
                assert_eq!(best.plaintext[i], plain[i], "position {i}");
            }
        }
        assert_eq!(&best.plaintext[off..off + 16], &crib_text[..]);
        let wrong = best.plaintext.iter().zip(&plain).filter(|(a, b)| a != b).count();
        eprintln!(
            "unresolved letters {}, plaintext errors {wrong}/{}: {}",
            alphabet::mask_to_string(best.unresolved),
            plain.len(),
            best.plaintext_string()
        );
    }

    #[test]
    fn bigram_score_separates_german_from_random() {
        let german = clean("DIEWETTERVORHERSAGEFUERMORGENISTKLARUNDWINDSTILLXANDERKUESTEREGEN");
        let random = clean("QXJZVKWPYBMFQGHZXJTKVWLPCNRYQZXMJBVWKPLTGHFDQZXYVBNMJKWPQRZTXLV");
        let (g, r) = (score_german(&german), score_german(&random));
        assert!(g > -2.9 && r < -3.5 && g > r + 1.0, "german {g:.2}, random {r:.2}");
    }
}
