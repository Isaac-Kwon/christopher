//! Real wartime messages from data/vectors/historical.json.

use christopher_core::alphabet;
use christopher_core::check::score_german;
use christopher_core::enigma::{Enigma, Key, Model, Plugboard};

struct Vector {
    id: String,
    key: Key,
    ciphertext: Vec<u8>,
    plaintext: Vec<u8>,
}

fn load() -> Vec<Vector> {
    let src = include_str!("../../../data/vectors/historical.json");
    let json: serde_json::Value = serde_json::from_str(src).expect("valid JSON");
    json.as_array()
        .expect("array")
        .iter()
        .map(|v| {
            let s = |k: &str| v[k].as_str().unwrap_or("").to_string();
            let rotors: Vec<String> = v["rotors"].as_array().unwrap().iter().map(|r| r.as_str().unwrap().to_string()).collect();
            let key = Key {
                model: Model::parse(&s("machine")).unwrap(),
                reflector: s("reflector"),
                greek: v["greek"].as_str().map(|g| g.to_string()),
                rotors: [rotors[0].clone(), rotors[1].clone(), rotors[2].clone()],
                rings: alphabet::parse_letters(&s("rings")).unwrap(),
                positions: alphabet::parse_letters(&s("start")).unwrap(),
                plugboard: Plugboard::from_pairs(&s("plugboard")).unwrap(),
            };
            Vector {
                id: s("id"),
                key,
                ciphertext: alphabet::clean(&s("ciphertext")),
                plaintext: alphabet::clean(&s("plaintext")),
            }
        })
        .collect()
}

#[test]
fn historical_messages_decrypt() {
    let vectors = load();
    assert!(!vectors.is_empty());
    for v in &vectors {
        let plain = Enigma::new(&v.key).unwrap().process(&v.ciphertext);
        if v.plaintext.is_empty() {
            // No published plaintext: the decryption must score clearly better than the
            // ciphertext itself under the German letter-frequency model.
            let (s, sc) = (score_german(&plain), score_german(&v.ciphertext));
            eprintln!("{}: score {s:.2} (ciphertext {sc:.2}): {}", v.id, alphabet::to_string(&plain));
            assert!(s > sc + 0.3, "{}: decryption does not look like German", v.id);
        } else {
            assert_eq!(alphabet::to_string(&plain), alphabet::to_string(&v.plaintext), "{}", v.id);
        }
    }
}

#[test]
fn m4_triton_is_present() {
    assert!(load().iter().any(|v| v.key.model == Model::M4 && !v.plaintext.is_empty()));
}
