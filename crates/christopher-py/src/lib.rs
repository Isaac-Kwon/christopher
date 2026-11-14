//! Thin PyO3 bindings for `christopher-core` (module `christopher._core`).
//!
//! The binding layer is deliberately dumb: letters cross the boundary as `str` (or `u8` 0..26 inside
//! tuples) and every structure is a plain tuple. `python/christopher` turns those into the
//! operator-facing classes. Only the Enigma is a stateful class. Long computations (bombe runs,
//! search, checking) release the GIL with `Python::detach`.
//!
//! Wire formats (all letters are `u8` 0..26, A = 0):
//! * menu   `(offset, crib, edges, test_letter, input_wire)` with `edges = [(plain, cipher, pos)]`
//! * order  `(reflector, greek | None, [left, middle, right])` (rotor/reflector names)
//! * stop   `(position, test_register)` with `position = (greek, left, middle, right)` core offsets

use christopher_core::alphabet;
use christopher_core::bombe::{BombeOptions, Plugged, Position, Stop, WheelOrder};
use christopher_core::check::{self, Solution};
use christopher_core::enigma::{Enigma, Key, Model};
use christopher_core::menu::{self, Edge, Menu};
use christopher_core::search::{self, SearchOptions};
use christopher_core::Error;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

type MenuT = (usize, String, Vec<(u8, u8, u16)>, u8, u8);
type OrderT = (String, Option<String>, [String; 3]);
type PosT = (u8, u8, u8, u8);
type StopT = (PosT, u32);
/// `(order, position, test_register, turnover_at)` as reported by a search.
type FullStopT = (OrderT, PosT, u32, Option<u16>);
/// `(rings, positions, plugboard, plaintext, unresolved mask, score, stop position,
/// guessed mask, turnover_at)`.
type SolutionT = (String, String, String, String, u32, f64, PosT, u32, Option<u16>);
/// `(order, solution)` as reported by a search.
type FullSolutionT = (OrderT, SolutionT);

fn err(e: Error) -> PyErr {
    PyValueError::new_err(e.to_string())
}

fn order_to_py(o: &WheelOrder) -> OrderT {
    (
        o.reflector.name.clone(),
        o.greek.map(|g| g.name.clone()),
        o.rotors.map(|r| r.name.clone()),
    )
}

fn order_from_py(o: &OrderT) -> PyResult<WheelOrder> {
    let rotors = [o.2[0].as_str(), o.2[1].as_str(), o.2[2].as_str()];
    WheelOrder::new(&o.0, o.1.as_deref(), rotors).map_err(err)
}

fn pos_to_py(p: Position) -> PosT {
    (p.greek, p.left, p.middle, p.right)
}

fn pos_from_py(p: PosT) -> Position {
    Position { greek: p.0, left: p.1, middle: p.2, right: p.3 }
}

fn menu_to_py(m: &Menu) -> MenuT {
    (
        m.offset,
        alphabet::to_string(&m.crib),
        m.edges.iter().map(|e| (e.plain, e.cipher, e.pos)).collect(),
        m.test_letter,
        m.input_wire,
    )
}

fn menu_from_py(m: &MenuT) -> PyResult<Menu> {
    let crib = alphabet::parse_letters(&m.1).map_err(err)?;
    let mut edges = Vec::with_capacity(m.2.len());
    for &(plain, cipher, pos) in &m.2 {
        if plain >= 26 || cipher >= 26 || plain == cipher {
            return Err(PyValueError::new_err(format!("invalid menu edge ({plain}, {cipher})")));
        }
        edges.push(Edge { plain, cipher, pos });
    }
    if m.3 >= 26 || m.4 >= 26 {
        return Err(PyValueError::new_err("test letter and input wire must be 0..26"));
    }
    Ok(Menu { offset: m.0, crib, edges, test_letter: m.3, input_wire: m.4 })
}

fn solution_to_py(s: &Solution) -> SolutionT {
    (
        alphabet::to_string(&s.key.rings),
        alphabet::to_string(&s.key.positions),
        s.key.plugboard.to_pairs_string(),
        s.plaintext_string(),
        s.unresolved,
        s.score,
        pos_to_py(s.stop_position),
        s.guessed,
        s.turnover_at,
    )
}

fn bombe_opts(diagonal_board: bool, turnover_at: Option<u16>) -> BombeOptions {
    BombeOptions { diagonal_board, turnover_at }
}

fn orders_from_py(orders: &[OrderT]) -> PyResult<Vec<WheelOrder>> {
    orders.iter().map(order_from_py).collect()
}

// ---------------------------------------------------------------------------------------------
// Enigma
// ---------------------------------------------------------------------------------------------

/// A mutable Enigma machine (`rings`, `positions`, `plugboard` are strict letter strings).
#[pyclass(name = "Enigma", module = "christopher._core")]
struct PyEnigma(Enigma);

#[pymethods]
impl PyEnigma {
    #[new]
    #[pyo3(signature = (model, reflector, greek, rotors, rings, positions, plugboard))]
    fn new(
        model: &str,
        reflector: &str,
        greek: Option<&str>,
        rotors: [String; 3],
        rings: &str,
        positions: &str,
        plugboard: &str,
    ) -> PyResult<Self> {
        let r = [rotors[0].as_str(), rotors[1].as_str(), rotors[2].as_str()];
        let key = Key::parse(model, reflector, greek, r, rings, positions, plugboard).map_err(err)?;
        Ok(PyEnigma(Enigma::new(&key).map_err(err)?))
    }

    /// Encipher/decipher `text` (non-letters dropped), stepping the rotors.
    fn process(&mut self, text: &str) -> String {
        self.0.process_str(text)
    }

    /// Window letters, greek first on M4.
    fn window(&self) -> String {
        self.0.window()
    }

    fn set_positions(&mut self, positions: &str) -> PyResult<()> {
        let p = alphabet::parse_letters(positions).map_err(err)?;
        self.0.set_positions(&p).map_err(err)
    }

    /// Core offsets (window minus ring), what the bombe searches.
    fn cores(&self) -> Vec<u8> {
        self.0.cores()
    }

    fn model(&self) -> &'static str {
        self.0.model.name()
    }
}

// ---------------------------------------------------------------------------------------------
// Menus
// ---------------------------------------------------------------------------------------------

/// Offsets where the crib can sit (no letter enciphered to itself).
#[pyfunction]
fn possible_offsets(ciphertext: &str, crib: &str) -> PyResult<Vec<usize>> {
    let ct = alphabet::clean(ciphertext);
    let cr = alphabet::parse_letters(crib).map_err(err)?;
    Ok(menu::possible_offsets(&ct, &cr))
}

/// Full menu: one edge per crib letter.
#[pyfunction]
fn menu_from_crib(ciphertext: &str, crib: &str, offset: usize) -> PyResult<MenuT> {
    let ct = alphabet::clean(ciphertext);
    let cr = alphabet::parse_letters(crib).map_err(err)?;
    Ok(menu_to_py(&Menu::from_crib(&ct, &cr, offset).map_err(err)?))
}

/// Loop-rich sub-menu with at most `max_edges` edges.
#[pyfunction]
fn menu_select(menu: MenuT, max_edges: usize) -> PyResult<MenuT> {
    Ok(menu_to_py(&menu_from_py(&menu)?.select(max_edges)))
}

/// `(loops, span, components)`; components are letter strings, largest first.
#[pyfunction]
fn menu_info(menu: MenuT) -> PyResult<(usize, usize, Vec<String>)> {
    let m = menu_from_py(&menu)?;
    Ok((m.loops(), m.span(), m.components().into_iter().map(alphabet::mask_to_string).collect()))
}

// ---------------------------------------------------------------------------------------------
// Bombe
// ---------------------------------------------------------------------------------------------

/// All wheel orders of a model, as `(reflector, greek, rotors)` tuples.
#[pyfunction]
fn all_orders(model: &str) -> PyResult<Vec<OrderT>> {
    let model = Model::parse(model).map_err(err)?;
    Ok(WheelOrder::all(model).iter().map(order_to_py).collect())
}

/// Run the bombe over each order; returns `(positions_tested, stops)` per order, same order.
#[pyfunction]
#[pyo3(signature = (menu, orders, diagonal_board=true, turnover_at=None, parallel=true))]
fn run_bombe(
    py: Python<'_>,
    menu: MenuT,
    orders: Vec<OrderT>,
    diagonal_board: bool,
    turnover_at: Option<u16>,
    parallel: bool,
) -> PyResult<Vec<(u64, Vec<StopT>)>> {
    let menu = menu_from_py(&menu)?;
    let orders = orders_from_py(&orders)?;
    let opts = bombe_opts(diagonal_board, turnover_at);
    let results = py.detach(|| search::run_orders(&menu, &orders, &opts, parallel));
    Ok(results
        .iter()
        .map(|r| (r.positions, r.stops.iter().map(|s| (pos_to_py(s.position), s.test_register)).collect()))
        .collect())
}

/// Checking machine: recover full keys from one stop.
#[pyfunction]
#[pyo3(signature = (ciphertext, menu, order, stop, diagonal_board=true, turnover_at=None))]
fn check_stop(
    py: Python<'_>,
    ciphertext: &str,
    menu: MenuT,
    order: OrderT,
    stop: StopT,
    diagonal_board: bool,
    turnover_at: Option<u16>,
) -> PyResult<Vec<SolutionT>> {
    let menu = menu_from_py(&menu)?;
    if menu.crib.is_empty() {
        return Err(PyValueError::new_err("checking needs the crib; build the menu with Menu.from_crib"));
    }
    let ct = alphabet::clean(ciphertext);
    let stop = Stop {
        wheel_order: order_from_py(&order)?,
        position: pos_from_py(stop.0),
        test_register: stop.1,
        turnover_at,
    };
    let sols = py.detach(|| check::check_stop(&ct, &menu, &stop, diagonal_board));
    Ok(sols.iter().map(solution_to_py).collect())
}

/// Full 676-wire state at one drum position, as 26 bitmasks (cable `a`, bit `w` = wire `w` live).
#[pyfunction]
#[pyo3(signature = (menu, order, position, input_wire=None, diagonal_board=true, turnover_at=None))]
fn trace(
    menu: MenuT,
    order: OrderT,
    position: PosT,
    input_wire: Option<u8>,
    diagonal_board: bool,
    turnover_at: Option<u16>,
) -> PyResult<Vec<u32>> {
    let m = menu_from_py(&menu)?;
    let wire = input_wire.unwrap_or(m.input_wire);
    if wire >= 26 {
        return Err(PyValueError::new_err("input wire must be 0..26"));
    }
    let plugged = Plugged::new(&m);
    let live = plugged.trace(&order_from_py(&order)?, pos_from_py(position), wire, &bombe_opts(diagonal_board, turnover_at));
    Ok(live.to_vec())
}

/// Whole pipeline: bombe over the orders, then the checking machine on every stop.
///
/// Returns `(orders_tried, positions_tested, stops, solutions)`, solutions best first.
#[pyfunction]
#[pyo3(signature = (ciphertext, menu, model, orders=None, diagonal_board=true, turnover_hypotheses=false, parallel=true))]
#[allow(clippy::too_many_arguments)] // mirrors the Python keyword arguments
fn run_search(
    py: Python<'_>,
    ciphertext: &str,
    menu: MenuT,
    model: &str,
    orders: Option<Vec<OrderT>>,
    diagonal_board: bool,
    turnover_hypotheses: bool,
    parallel: bool,
) -> PyResult<(usize, u64, Vec<FullStopT>, Vec<FullSolutionT>)> {
    let menu = menu_from_py(&menu)?;
    let ct = alphabet::clean(ciphertext);
    let orders = orders.as_deref().map(orders_from_py).transpose()?;
    let opts = SearchOptions {
        orders,
        model: Model::parse(model).map_err(err)?,
        diagonal_board,
        turnover_hypotheses,
        parallel,
    };
    let report = py
        .detach(|| search::search(&ct, &menu, &opts))
        .map_err(err)?;
    let stops = report
        .stops
        .iter()
        .map(|s| (order_to_py(&s.wheel_order), pos_to_py(s.position), s.test_register, s.turnover_at))
        .collect();
    let sols = report.solutions.iter().map(|s| (order_to_py(&s.wheel_order), solution_to_py(s))).collect();
    Ok((report.orders_tried, report.positions_tested, stops, sols))
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyEnigma>()?;
    m.add_function(wrap_pyfunction!(possible_offsets, m)?)?;
    m.add_function(wrap_pyfunction!(menu_from_crib, m)?)?;
    m.add_function(wrap_pyfunction!(menu_select, m)?)?;
    m.add_function(wrap_pyfunction!(menu_info, m)?)?;
    m.add_function(wrap_pyfunction!(all_orders, m)?)?;
    m.add_function(wrap_pyfunction!(run_bombe, m)?)?;
    m.add_function(wrap_pyfunction!(check_stop, m)?)?;
    m.add_function(wrap_pyfunction!(trace, m)?)?;
    m.add_function(wrap_pyfunction!(run_search, m)?)?;
    Ok(())
}
