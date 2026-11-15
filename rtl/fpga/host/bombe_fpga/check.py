"""Compare FPGA stops with the software bombe (christopher.Bombe.run) for the same menu / wheel orders."""

from __future__ import annotations

from dataclasses import dataclass, field


def _key(stop):
    return (str(stop.wheel_order), stop.position, stop.test_register)


@dataclass
class CheckReport:
    fpga: list
    software: list
    only_fpga: list = field(default_factory=list)
    only_software: list = field(default_factory=list)
    same_order: bool = True

    @property
    def ok(self) -> bool:
        return not self.only_fpga and not self.only_software and self.same_order

    def __str__(self) -> str:
        head = f"FPGA {len(self.fpga)} stops, software {len(self.software)} stops: "
        if self.ok:
            return head + "IDENTICAL (same stops, same order)"
        lines = [head + "MISMATCH"]
        lines += [f"  only FPGA:     {k}" for k in self.only_fpga[:20]]
        lines += [f"  only software: {k}" for k in self.only_software[:20]]
        if not self.only_fpga and not self.only_software:
            lines.append("  same stops but in a different order")
        return "\n".join(lines)


def compare(fpga_stops, sw_stops) -> CheckReport:
    f, s = [_key(x) for x in fpga_stops], [_key(x) for x in sw_stops]
    return CheckReport(
        fpga_stops, sw_stops,
        only_fpga=[k for k in f if k not in set(s)],
        only_software=[k for k in s if k not in set(f)],
        same_order=f == s,
    )


def check(fpga, menu, wheel_orders="all", model: str = "enigma-i", diagonal_board: bool = True,
          turnover_at: int | None = None, **farm_kw) -> CheckReport:
    """Run on the FPGA and in software and compare (the software run is the reference)."""
    from christopher.bombe import Bombe

    from bombe_fpga.farm import Farm

    hw = Farm(fpga).run(menu, wheel_orders, model, diagonal_board, turnover_at, **farm_kw)
    sw = Bombe(model, diagonal_board).plug_up(menu).run(wheel_orders, turnover_at=turnover_at)
    return compare(hw, sw)
