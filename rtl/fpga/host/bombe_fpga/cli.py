"""Command line: ``python -m bombe_fpga {info,run,check} ...`` (run from rtl/fpga/host or install the dir).

  python -m bombe_fpga --uart COM5 info
  python -m bombe_fpga --uart COM5 run   --ciphertext CT --crib WETTERVORHERSAGE --offset 24 --orders "B II-IV-V"
  python -m bombe_fpga --uart COM5 check --ciphertext CT --crib ... --offset 24 --orders all
  python -m bombe_fpga --mmio 0x40000000 info          (on a PYNQ board, with pynq installed)
  python -m bombe_fpga demo                            (exhibit window on a fake board, no hardware)
  python -m bombe_fpga --uart COM5 demo --crib ...     (exhibit window on the real board)

``check`` runs the same menu / wheel orders through christopher.Bombe.run on the PC and compares.
"""

from __future__ import annotations

import argparse
import sys
import time


def _transport(args):
    from bombe_fpga.transport import MmioTransport, UartTransport

    if args.uart:
        return UartTransport(args.uart, baud=args.baud)
    if args.mmio is not None:
        return MmioTransport(int(args.mmio, 0))
    raise SystemExit("give --uart PORT (PC) or --mmio BASE (PYNQ)")


def _menu(args):
    from christopher import Menu

    menu = Menu.from_crib(args.ciphertext, args.crib, args.offset)
    return menu.select(args.max_edges) if args.max_edges else menu


def _orders(args):
    return "all" if args.orders.lower() == "all" else [o.strip() for o in args.orders.split(",")]


DEMO_PLAIN = "ANXOBERKOMMANDODERWEHRMACHTXWETTERVORHERSAGEFUERDIENORDSEEXHEUTEKLARXWINDAUSWESTXSTAERKEDREI"
DEMO_CRIB = "WETTERVORHERSAGE"


def _demo(args) -> int:
    from bombe_fpga.driver import FpgaBombe, config_from
    from bombe_fpga.visual import DemoApp, DemoController

    fake = not (args.uart or args.mmio is not None)
    cfg = None
    menu = None
    if fake or args.ciphertext:
        from christopher import Enigma, Menu, WheelOrder

        ct = args.ciphertext
        if ct is None:   # example message enciphered with a secret key, as in the host tests
            ct = Enigma("enigma-i", rotors=("IV", "I", "V"), reflector="B", rings="AKH", positions="QWE",
                        plugboard="AQ BZ CR DM EV FW GK HN IU JP").encrypt(DEMO_PLAIN)
        offset = args.offset if args.offset is not None else DEMO_PLAIN.index(DEMO_CRIB)
        menu = Menu.from_crib(ct, args.crib, offset)
        menu = menu.select(args.max_edges) if args.max_edges else menu
        cfg = config_from(menu, WheelOrder.parse(args.order))
    if fake:
        from bombe_fpga.fake import FakeDemoArray, software_snapshot, software_sweep, synthetic_model

        sweep, snap = synthetic_model() if args.synthetic else (software_sweep(menu), software_snapshot(menu))
        bus = FakeDemoArray(sweep, snap)
        print("fake board (software bombe)" if not args.synthetic else "fake board (synthetic wires)")
    else:
        bus = _transport(args)
    with bus if hasattr(bus, "__enter__") else _Null(bus):
        fpga = FpgaBombe(bus)
        if cfg is not None:
            fpga.engines[0].configure(cfg)
        ctrl = DemoController(fpga)
        if fake:
            ctrl.start()          # the fake board has nothing else to do: begin right away
        app = DemoApp(ctrl, fullscreen=args.fullscreen)
        app.run(max_frames=args.smoke)
    return 0


class _Null:
    def __init__(self, obj):
        self.obj = obj

    def __enter__(self):
        return self.obj

    def __exit__(self, *a):
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bombe_fpga", description=__doc__.split("\n")[0])
    ap.add_argument("--uart", help="serial port of the Nexys Video, e.g. COM5 or /dev/ttyUSB1")
    ap.add_argument("--baud", type=int, default=921_600)
    ap.add_argument("--mmio", help="AXI base address of the axil_bridge (PYNQ), e.g. 0x40000000")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="read ID/INFO and engine status")
    for name in ("run", "check"):
        p = sub.add_parser(name)
        p.add_argument("--ciphertext", required=True)
        p.add_argument("--crib", required=True)
        p.add_argument("--offset", type=int, required=True)
        p.add_argument("--model", default="enigma-i")
        p.add_argument("--orders", default="all", help='"all" or comma separated, e.g. "B II-IV-V,C I-II-III"')
        p.add_argument("--max-edges", type=int, default=12, help="trim the menu (0 = keep all)")
        p.add_argument("--no-diagonal", action="store_true")
        p.add_argument("--turnover-at", type=int)
        p.add_argument("--timeout", type=float)
    d = sub.add_parser("demo", help="exhibit window (Tkinter): dials, menu, 676 wires; fake board unless --uart/--mmio")
    d.add_argument("--ciphertext", help="default: built-in example message")
    d.add_argument("--crib", default=DEMO_CRIB)
    d.add_argument("--offset", type=int)
    d.add_argument("--order", default="B IV-I-V", help="wheel order to sweep, e.g. 'B IV-I-V'")
    d.add_argument("--max-edges", type=int, default=12)
    d.add_argument("--synthetic", action="store_true", help="fake board without the software bombe (display only)")
    d.add_argument("--fullscreen", action="store_true")
    d.add_argument("--smoke", type=int, metavar="FRAMES", help="draw FRAMES frames, then exit (self test)")
    args = ap.parse_args(argv)
    if args.cmd == "demo":
        return _demo(args)

    from bombe_fpga.driver import FpgaBombe
    from bombe_fpga.farm import Farm

    with _transport(args) as bus:
        fpga = FpgaBombe(bus)
        print(fpga)
        if args.cmd == "info":
            for e in fpga.engines:
                print(f"engine {e.index}: status {e.status():#x}")
            return 0
        menu = _menu(args)
        print(menu)
        t0 = time.monotonic()
        kw = dict(model=args.model, diagonal_board=not args.no_diagonal, turnover_at=args.turnover_at,
                  timeout=args.timeout)
        if args.cmd == "run":
            farm = Farm(fpga)
            stops = farm.run(menu, _orders(args), progress=lambda d, n: print(f"\r{d}/{n} orders", end="", file=sys.stderr), **kw)
            print(f"\n{len(stops)} stops, {farm.positions_tested} positions, {time.monotonic() - t0:.2f} s")
            for s in stops:
                print(s)
            return 0
        from bombe_fpga.check import check

        rep = check(fpga, menu, _orders(args), **kw)
        print(rep)
        return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
