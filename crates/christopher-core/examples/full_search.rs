//! Full-scale searches: every wheel order of Enigma I and of M4.
//!
//!     cargo run --release -p christopher-core --example full_search [enigma-i|m4]

use std::time::Instant;

use christopher_core::alphabet::{self, clean};
use christopher_core::enigma::{Enigma, Key, Model};
use christopher_core::menu::Menu;
use christopher_core::search::{search, SearchOptions};

const TEXT: &str = "ANXOBERKOMMANDODERWEHRMACHTXWETTERVORHERSAGEFUERDIENORDSEEXHEUTEKLAR\
                    XWINDAUSWESTXSTAERKEDREIXSICHTWEITEZEHNSEEMEILENXENDE";

fn main() {
    let which = std::env::args().nth(1).unwrap_or_else(|| "m4".into());
    let (model, key) = match which.as_str() {
        "enigma-i" => (
            Model::EnigmaI,
            Key::parse("enigma-i", "B", None, ["III", "V", "II"], "GMC", "KDC", "AQ BZ CR DM EV FW GK HN IU JP").unwrap(),
        ),
        _ => (
            Model::M4,
            Key::parse("m4", "B-THIN", Some("GAMMA"), ["VI", "I", "II"], "AAEK", "RZDC", "AT BL DF GJ HM NW OP QY RZ VX").unwrap(),
        ),
    };
    let plain = clean(TEXT);
    let cipher = Enigma::new(&key).unwrap().process(&plain);
    let crib = clean("WETTERVORHERSAGEFUERDIE");
    let menu = Menu::from_crib(&cipher, &crib, 28).unwrap().select(14);
    println!("{}", menu.describe());
    println!("threads: {}", rayon::current_num_threads());

    let t = Instant::now();
    let report = search(&cipher, &menu, &SearchOptions { model, ..Default::default() }).unwrap();
    let dt = t.elapsed();
    println!(
        "{}: {} wheel orders, {} positions, {} stops, {} solutions in {:.2?} ({:.1} M positions/s)",
        model.name(),
        report.orders_tried,
        report.positions_tested,
        report.stops.len(),
        report.solutions.len(),
        dt,
        report.positions_tested as f64 / dt.as_secs_f64() / 1e6
    );
    for s in report.solutions.iter().take(3) {
        println!(
            "  {}  rings {}  start {}  plugs {}  score {:.2}\n  {}",
            s.wheel_order.name(),
            alphabet::to_string(&s.key.rings),
            alphabet::to_string(&s.key.positions),
            s.key.plugboard.to_pairs_string(),
            s.score,
            s.plaintext_string()
        );
    }
}
