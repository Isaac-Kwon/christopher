"""Command line: ``python -m menu_cards <build|find|card|verify> ...`` (run from ``tools/``)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import card, engine, record
from .engine import Spec
from .scenarios import DEFAULT_SET

ROOT = Path(__file__).resolve().parents[2]
JSON_DIR = ROOT / "hardware" / "common" / "menu-cards"


def default_pdf_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "christopher" / "menu-cards"


def save(rec: dict, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{rec['id']}.json"
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def run_one(spec: Spec, seed: int, workers: int, json_dir: Path, pdf_dir: Path | None, max_attempts: int):
    c, ev = engine.generate(spec, seed, max_attempts=max_attempts, verbose=True, workers=workers)
    rec = record.make_record(c, ev, seed)
    problems = record.verify_record(rec)
    if problems:
        raise SystemExit(f"{spec.name}: verification failed: {problems}")
    save(rec, json_dir)
    if pdf_dir is not None:
        print("  pdf:", card.build_pdf(rec, pdf_dir))
    r = rec["run"]
    print(f"  {rec['mode']} {rec['menu']['n_edges']}e/{rec['menu']['n_letters']}l/{rec['menu']['loops']}loops  "
          f"order {rec['wheel_order']['text']}  true stop {rec['true_stop']['drums_core_letters']}  "
          f"false stops: mode {r['false_stops']}, true order full {r['false_stops_true_order_full_run']}, "
          f"all 60 orders {r['false_stops_all_60_orders_full_run']}  ({rec['search_seconds']} s, {rec['attempts']} attempts)")
    return rec


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="menu_cards", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="generate the default set (JSON + PDF cards)")
    b.add_argument("--only", nargs="*", help="scenario names to build")
    f = sub.add_parser("find", help="search one scenario with your own parameters")
    f.add_argument("--name", required=True)
    f.add_argument("--mode", choices=["slice", "full"], required=True)
    f.add_argument("--edges", type=int, required=True, help="scrambler units = menu edges")
    f.add_argument("--letters", type=int, required=True)
    f.add_argument("--loops", type=int, help="optional check: edges - letters + 1")
    f.add_argument("--max-span", type=int, default=12)
    f.add_argument("--false-min", type=int, default=0)
    f.add_argument("--false-max", type=int, default=1)
    f.add_argument("--seed", type=int, default=1)
    c = sub.add_parser("card", help="rebuild the PDF card(s) from JSON record(s)")
    c.add_argument("json", nargs="+")
    v = sub.add_parser("verify", help="re-verify JSON record(s) with the software core")
    v.add_argument("json", nargs="+")
    for p in (b, f, c):
        p.add_argument("--json-dir", type=Path, default=JSON_DIR)
        p.add_argument("--pdf-dir", type=Path, default=default_pdf_dir(), help="outside the repository")
        p.add_argument("--no-pdf", action="store_true")
    for p in (b, f):
        p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
        p.add_argument("--max-attempts", type=int, default=400000)
    a = ap.parse_args(argv)

    if a.cmd == "build":
        todo = [(s, seed) for s, seed in DEFAULT_SET if not a.only or s.name in a.only]
        recs = []
        for spec, seed in todo:
            recs.append(run_one(spec, seed, a.workers, a.json_dir, None if a.no_pdf else a.pdf_dir, a.max_attempts))
        if not a.only:
            idx = [dict(id=r["id"], mode=r["mode"], seed=r["seed"], edges=r["menu"]["n_edges"],
                        letters=r["menu"]["n_letters"], loops=r["menu"]["loops"],
                        wheel_order=r["wheel_order"]["text"], false_stops=r["run"]["false_stops"]) for r in recs]
            (a.json_dir / "index.json").write_text(json.dumps(idx, indent=2) + "\n", encoding="utf-8")
    elif a.cmd == "find":
        spec = Spec(a.name, a.mode, a.edges, a.letters, a.max_span, a.false_min, a.false_max)
        if a.loops is not None and a.loops != spec.loops:
            sys.exit(f"edges {a.edges} and letters {a.letters} give {spec.loops} loops, not {a.loops}")
        run_one(spec, a.seed, a.workers, a.json_dir, None if a.no_pdf else a.pdf_dir, a.max_attempts)
    elif a.cmd == "card":
        for p in a.json:
            print(card.build_pdf(json.loads(Path(p).read_text(encoding="utf-8")), a.pdf_dir))
    elif a.cmd == "verify":
        bad = False
        for p in a.json:
            probs = record.verify_record(json.loads(Path(p).read_text(encoding="utf-8")))
            print(p, "OK" if not probs else probs)
            bad |= bool(probs)
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
