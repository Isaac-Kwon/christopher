//! Full search: run the bombe over many wheel orders and check every stop — what a bombe hut did
//! for one day's key, with all bombes running different wheel orders at once.
//!
//! Work is split into tasks of (wheel order, turnover hypothesis, greek position) so that even a
//! handful of four-rotor wheel orders keeps every CPU core busy.

use rayon::prelude::*;

use crate::bombe::{run_slice, BombeOptions, Plugged, RunResult, Stop, WheelOrder};
use crate::check::{check_stop, Solution};
use crate::enigma::Model;
use crate::error::{Error, Result};
use crate::menu::Menu;

#[derive(Debug, Clone)]
pub struct SearchOptions {
    /// Wheel orders to try; `None` = every wheel order of `model`.
    pub orders: Option<Vec<WheelOrder>>,
    pub model: Model,
    pub diagonal_board: bool,
    /// Also try a middle-rotor turnover at every crib position (multiplies the work by the span).
    pub turnover_hypotheses: bool,
    /// Use all CPU cores.
    pub parallel: bool,
    /// Slice run: keep the right (slow) drum at this core offset (see [`BombeOptions::fixed_right`]).
    pub fixed_right: Option<u8>,
}

impl Default for SearchOptions {
    fn default() -> Self {
        SearchOptions {
            orders: None,
            model: Model::EnigmaI,
            diagonal_board: true,
            turnover_hypotheses: false,
            parallel: true,
            fixed_right: None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct SearchReport {
    pub orders_tried: usize,
    pub positions_tested: u64,
    pub stops: Vec<Stop>,
    pub solutions: Vec<Solution>,
}

/// Greek-wheel slices of one wheel order: 26 for M4, a single whole-order slice otherwise.
fn slices(order: &WheelOrder) -> Vec<Option<u8>> {
    if order.greek.is_some() {
        (0..26).map(Some).collect()
    } else {
        vec![None]
    }
}

fn par_map<T: Sync, R: Send>(items: &[T], parallel: bool, f: impl Fn(&T) -> R + Sync + Send) -> Vec<R> {
    if parallel {
        items.par_iter().map(f).collect()
    } else {
        items.iter().map(f).collect()
    }
}

/// Run the bombe (no checking) over several wheel orders; one [`RunResult`] per order, same order.
pub fn run_orders(menu: &Menu, orders: &[WheelOrder], opts: &BombeOptions, parallel: bool) -> Vec<RunResult> {
    let plugged = Plugged::new(menu);
    let tasks: Vec<(usize, Option<u8>)> =
        orders.iter().enumerate().flat_map(|(i, o)| slices(o).into_iter().map(move |g| (i, g))).collect();
    let parts = par_map(&tasks, parallel, |&(i, g)| (i, run_slice(&plugged, &orders[i], opts, g)));
    let mut out: Vec<RunResult> = orders.iter().map(|_| RunResult { stops: Vec::new(), positions: 0 }).collect();
    for (i, r) in parts {
        out[i].positions += r.positions;
        out[i].stops.extend(r.stops);
    }
    out
}

/// Run the bombe with `menu` over the selected wheel orders and check every stop.
/// Solutions are sorted best first (by plaintext score).
pub fn search(ciphertext: &[u8], menu: &Menu, opts: &SearchOptions) -> Result<SearchReport> {
    if menu.edges.is_empty() {
        return Err(Error::Config("menu has no edges".into()));
    }
    let orders = opts.orders.clone().unwrap_or_else(|| WheelOrder::all(opts.model));
    let mut turnovers: Vec<Option<u16>> = vec![None];
    if opts.turnover_hypotheses {
        let lo = menu.edges.iter().map(|e| e.pos).min().unwrap_or(0);
        let hi = menu.edges.iter().map(|e| e.pos).max().unwrap_or(0);
        turnovers.extend((lo + 1..=hi).map(Some));
    }
    let plugged = Plugged::new(menu);
    let tasks: Vec<(usize, Option<u16>, Option<u8>)> = orders
        .iter()
        .enumerate()
        .flat_map(|(i, o)| {
            let turnovers = &turnovers;
            slices(o).into_iter().flat_map(move |g| turnovers.iter().map(move |&t| (i, t, g)))
        })
        .collect();
    let results = par_map(&tasks, opts.parallel, |&(i, t, g)| {
        let bo = BombeOptions { diagonal_board: opts.diagonal_board, turnover_at: t, fixed_right: opts.fixed_right };
        let res = run_slice(&plugged, &orders[i], &bo, g);
        let sols: Vec<Solution> =
            res.stops.iter().flat_map(|s| check_stop(ciphertext, menu, s, opts.diagonal_board)).collect();
        (res, sols)
    });
    let mut report = SearchReport { orders_tried: orders.len(), positions_tested: 0, stops: Vec::new(), solutions: Vec::new() };
    for (res, sols) in results {
        report.positions_tested += res.positions;
        report.stops.extend(res.stops);
        report.solutions.extend(sols);
    }
    // The bombe cannot tell Enigma I from M3 when only rotors I–V are used; report the model asked for.
    if opts.model == Model::M3 {
        for s in &mut report.solutions {
            if s.key.model == Model::EnigmaI {
                s.key.model = Model::M3;
            }
        }
    }
    report.solutions.sort_by(|a, b| b.score.total_cmp(&a.score));
    Ok(report)
}
