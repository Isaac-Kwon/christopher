//! End-to-end: encipher with a hidden key, then recover it with menu → bombe → checking.

use std::time::Instant;

use christopher_core::alphabet::{self, clean};
use christopher_core::bombe::{run, BombeOptions, WheelOrder};
use christopher_core::enigma::{Enigma, Key, Model};
use christopher_core::menu::Menu;
use christopher_core::search::{search, SearchOptions};

const TEXT: &str = "ANXOBERKOMMANDODERWEHRMACHTXWETTERVORHERSAGEFUERDIENORDSEEXHEUTEKLAR\
                    XWINDAUSWESTXSTAERKEDREIXSICHTWEITEZEHNSEEMEILENXENDE";

/// True core offsets at the first crib letter.
fn cores_at(key: &Key, offset: usize) -> Vec<u8> {
    let mut e = Enigma::new(key).unwrap();
    for _ in 0..=offset {
        e.step();
    }
    e.cores()
}

#[test]
fn enigma_i_full_search_recovers_key() {
    // Right rotor II turns over at E. Start C puts the right window on F..B during the 23-letter
    // crib (offset 28), so the middle rotor does not move inside the crib — the bombe's assumption.
    let key = Key::parse("enigma-i", "B", None, ["III", "V", "II"], "GMC", "KDC", "AQ BZ CR DM EV FW GK HN IU JP").unwrap();
    let plain = clean(TEXT);
    let cipher = Enigma::new(&key).unwrap().process(&plain);
    let crib = clean("WETTERVORHERSAGEFUERDIE");
    let off = 28;
    assert_eq!(&plain[off..off + crib.len()], &crib[..]);

    let menu = Menu::from_crib(&cipher, &crib, off).unwrap().select(14);
    let t = Instant::now();
    let report = search(&cipher, &menu, &SearchOptions { model: Model::EnigmaI, ..Default::default() }).unwrap();
    eprintln!(
        "{}\nEnigma I full search: {} wheel orders, {} positions, {} stops, {} solutions in {:.2?}",
        menu.describe(),
        report.orders_tried,
        report.positions_tested,
        report.stops.len(),
        report.solutions.len(),
        t.elapsed()
    );
    assert_eq!(report.orders_tried, 120);
    assert_eq!(report.positions_tested, 120 * 17576);
    let best = &report.solutions[0];
    assert_eq!(best.wheel_order.name(), "B III-V-II");
    let c = cores_at(&key, off);
    assert_eq!([best.stop_position.left, best.stop_position.middle, best.stop_position.right], [c[0], c[1], c[2]]);
    assert_eq!(best.key.plugboard, key.plugboard);
    assert_eq!(alphabet::to_string(&best.plaintext), alphabet::to_string(&plain));
}

#[test]
fn m4_bombe_finds_true_position() {
    // Right rotor II (turnover E), start C: no middle-rotor movement inside the crib.
    // (With VI–VIII on the right, two notches leave at most 13 turnover-free letters.)
    let key = Key::parse("m4", "B-THIN", Some("GAMMA"), ["VI", "I", "II"], "AAEK", "RZDC", "AT BL DF GJ HM NW OP QY RZ VX").unwrap();
    let plain = clean(TEXT);
    let cipher = Enigma::new(&key).unwrap().process(&plain);
    let crib = clean("WETTERVORHERSAGEFUERDIE");
    let off = 28;
    let menu = Menu::from_crib(&cipher, &crib, off).unwrap().select(14);
    let orders = vec![
        WheelOrder::new("B-THIN", Some("GAMMA"), ["VI", "I", "II"]).unwrap(),
        WheelOrder::new("B-THIN", Some("BETA"), ["VI", "I", "II"]).unwrap(),
        WheelOrder::new("C-THIN", Some("GAMMA"), ["I", "VI", "II"]).unwrap(),
    ];
    let t = Instant::now();
    let report = search(&cipher, &menu, &SearchOptions { orders: Some(orders), model: Model::M4, ..Default::default() }).unwrap();
    eprintln!(
        "M4: {} orders, {} positions, {} stops, {} solutions in {:.2?}",
        report.orders_tried,
        report.positions_tested,
        report.stops.len(),
        report.solutions.len(),
        t.elapsed()
    );
    assert_eq!(report.positions_tested, 3 * 26u64.pow(4));
    let best = &report.solutions[0];
    assert_eq!(best.wheel_order.name(), "B-THIN GAMMA VI-I-II");
    let c = cores_at(&key, off);
    let p = best.stop_position;
    assert_eq!([p.greek, p.left, p.middle, p.right], [c[0], c[1], c[2], c[3]]);
    assert_eq!(best.key.plugboard, key.plugboard);
    assert_eq!(alphabet::to_string(&best.plaintext), alphabet::to_string(&plain));
}

#[test]
fn turnover_inside_crib_needs_hypothesis() {
    // Right rotor III turns over at V; start so that the turnover falls inside the crib.
    let key = Key::parse("enigma-i", "B", None, ["I", "II", "III"], "AAA", "AAH", "AQ BZ CR DM EV FW").unwrap();
    let plain = clean(TEXT);
    let cipher = Enigma::new(&key).unwrap().process(&plain);
    let crib = clean("WETTERVORHERSAGEFUERDIE");
    let off = 28;
    let menu = Menu::from_crib(&cipher, &crib, off).unwrap().select(14);
    let order = WheelOrder::new("B", None, ["I", "II", "III"]).unwrap();
    let opts = |t| SearchOptions { orders: Some(vec![order]), turnover_hypotheses: t, ..Default::default() };
    let plain_run = search(&cipher, &menu, &opts(false)).unwrap();
    let with_t = search(&cipher, &menu, &opts(true)).unwrap();
    let found = |r: &christopher_core::search::SearchReport| r.solutions.iter().any(|s| s.plaintext[off..off + crib.len()] == crib[..]);
    assert!(found(&with_t), "turnover hypotheses should find the key");
    eprintln!("without turnover hypotheses found: {}", found(&plain_run));
}

#[test]
fn diagonal_board_reduces_stops() {
    let key = Key::parse("enigma-i", "C", None, ["IV", "II", "I"], "AAA", "QAB", "AQ BZ CR DM EV FW GK HN IU JP").unwrap();
    let plain = clean(TEXT);
    let cipher = Enigma::new(&key).unwrap().process(&plain);
    let crib = clean("WETTERVORHERSAGE");
    let menu = Menu::from_crib(&cipher, &crib, 28).unwrap().select(12);
    let order = WheelOrder::new("C", None, ["IV", "II", "I"]).unwrap();
    let with = run(&menu, &order, &BombeOptions { diagonal_board: true, turnover_at: None }).stops.len();
    let without = run(&menu, &order, &BombeOptions { diagonal_board: false, turnover_at: None }).stops.len();
    eprintln!("{}stops with diagonal board: {with}, without: {without}", menu.describe());
    assert!(with <= without);
    let _ = alphabet::to_string(&menu.crib);
}
