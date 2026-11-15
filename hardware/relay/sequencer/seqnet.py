"""KiCad netlist (kicadsexpr) reader used by the verification scripts (stdlib only)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def find_cli() -> str:
    return os.environ.get("KICAD_CLI") or shutil.which("kicad-cli") or "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"


def scratch_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    p = Path(base) / "christopher" / "sequencer"
    p.mkdir(parents=True, exist_ok=True)
    return p


def export_netlist(sch: Path | None = None, out: Path | None = None) -> Path:
    sch = sch or HERE / "sequencer.kicad_sch"
    out = out or scratch_dir() / "sequencer.net"
    subprocess.run([find_cli(), "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out), str(sch)],
                   check=True, stdout=subprocess.DEVNULL)
    return out


def tokenize(text: str):
    for m in re.finditer(r'\(|\)|"((?:[^"\\]|\\.)*)"|[^\s()"]+', text):
        yield m.group(0) if m.group(1) is None else ("s", m.group(1))


def parse(text: str):
    stack: list[list] = [[]]
    for tok in tokenize(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            lst = stack.pop()
            stack[-1].append(lst)
        else:
            stack[-1].append(tok[1] if isinstance(tok, tuple) else tok)
    return stack[0][0]


def find_all(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def find1(node, key):
    r = find_all(node, key)
    return r[0] if r else None


class Netlist:
    def __init__(self, text: str):
        tree = parse(text)
        self.parts: dict[str, dict] = {}
        for c in find_all(find1(tree, "components"), "comp"):
            ref = find1(c, "ref")[1]
            self.parts[ref] = dict(value=(find1(c, "value") or [0, ""])[1], fp=(find1(c, "footprint") or [0, ""])[1],
                                   desc=(find1(c, "description") or [0, ""])[1])
        self.nets: dict[str, list[tuple[str, str]]] = {}
        for n in find_all(find1(tree, "nets"), "net"):
            name = find1(n, "name")[1].lstrip("/")
            self.nets.setdefault(name, []).extend((find1(x, "ref")[1], find1(x, "pin")[1]) for x in find_all(n, "node"))
        self.pin_net: dict[tuple[str, str], str] = {}
        for name, nodes in self.nets.items():
            for nd in nodes:
                self.pin_net[nd] = name
        # D-sub shells: footprint pads SH have no symbol pin; they are CHASSIS (0003 7.1)
        ch = self.nets.get("CHASSIS")
        if ch is not None:
            for r, pt in sorted(self.parts.items()):
                if "DSUB" in pt["fp"]:
                    ch.append((r, "SH"))
                    self.pin_net[(r, "SH")] = "CHASSIS"

    def net(self, ref: str, pin: str):
        return self.pin_net.get((ref, pin))

    @classmethod
    def load(cls, path: Path | None = None):
        return cls(Path(path or export_netlist()).read_text(encoding="utf8"))
