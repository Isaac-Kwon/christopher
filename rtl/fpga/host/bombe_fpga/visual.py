"""Exhibit monitor for the FPGA bombe: drum dials, menu graph, 26x26 wire grid, test register lamps.

The FPGA has no moving parts, so this window is what visitors watch.  It talks to the engine's exhibit
registers (PACE, DEMO, STEP, DRUM, SNAP_*; see rtl/fpga/README.md) through any transport, including the
fake one:

    python -m bombe_fpga demo                      # fake board + software bombe, no hardware
    python -m bombe_fpga --uart COM5 demo          # real Nexys Video (menu must already be loaded)

``DemoController`` holds all logic and has no GUI dependency (unit-tested); ``DemoApp`` is the Tkinter view.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from dataclasses import dataclass, field

from bombe_fpga.driver import (
    REG, ST_BUSY, ST_DONE, ST_HOLD, ST_STOP, Snapshot, decode_drums, decode_stop, pace_for_rate,
    stecker_candidates,
)

SLIDER_MAX = 100


def slider_to_rate(value: float):
    """Slider 0..100 -> positions per second (0.5 .. ~450k); the last notch is None = full speed."""
    if value >= SLIDER_MAX:
        return None
    return 0.5 * 10 ** (value * 0.06)


def rate_to_slider(rate) -> float:
    if rate is None:
        return float(SLIDER_MAX)
    return max(0.0, min(SLIDER_MAX - 1.0, math.log10(rate / 0.5) / 0.06))


def letter(v: int) -> str:
    return chr(65 + v % 26)


def circle_layout(n: int, cx: float, cy: float, radius: float) -> list:
    """Node centres of an n-gon, first node at the top."""
    return [(cx + radius * math.sin(2 * math.pi * i / max(n, 1)), cy - radius * math.cos(2 * math.pi * i / max(n, 1)))
            for i in range(n)]


@dataclass
class MenuView:
    """The menu as the engine holds it: letters, links (a, b, crib position 1-based), test letter, input wire."""

    links: list
    letters: list
    test_letter: int
    input_wire: int
    greek: bool
    total_positions: int


@dataclass
class Frame:
    status: int = 0
    drums: tuple = (0, 0, 0, 0)
    snap: "Snapshot | None" = None
    positions: int = 0
    rate: float = 0.0                  # measured positions per second
    new_stops: list = field(default_factory=list)   # [(drums, test register)] popped this poll
    test_register: int = 0
    stale: bool = False                # snapshot could not be read coherently (sweep too fast)
    t: float = 0.0

    @property
    def running(self):
        return bool(self.status & ST_BUSY)

    @property
    def held(self):
        return bool(self.status & ST_HOLD)

    @property
    def done(self):
        return bool(self.status & ST_DONE) and not self.running


class DemoController:
    """Everything the visualizer does with one engine, independent of the GUI."""

    def __init__(self, fpga, engine: int = 0, clock=time.monotonic):
        self.fpga, self.eng, self.clock = fpga, fpga.engines[engine], clock
        self.clock_mhz = fpga.clock_mhz
        self.single = False
        self.pause_on_stop = True
        self.rate = 2.0                 # positions per second; None = full speed
        self._last = None               # (time, positions)
        self._smoothed = 0.0

    # --- configuration read back from the engine -------------------------------------------------
    def menu(self) -> MenuView:
        e = self.eng
        links = [(a, b, p + 1) for a, b, p in e.edges()]
        opt = e.rd(REG["OPTIONS"])
        letters = sorted({x for a, b, _ in links for x in (a, b)} | {opt & 31})
        greek = bool(e.rd(REG["ORDER"]) >> 2 & 1)
        return MenuView(links, letters, opt & 31, opt >> 8 & 31, greek, 26 ** (4 if greek else 3))

    # --- controls ----------------------------------------------------------------------------------
    def _push_mode(self):
        self.eng.set_demo(self.single, self.pause_on_stop)

    def apply_rate(self, rate) -> int:
        """Pace for ``rate`` positions/s (None = full speed); returns the PACE value."""
        self.rate = rate
        pace = 0 if rate is None else pace_for_rate(rate, self.clock_mhz)
        self.eng.set_pace(pace)
        return pace

    def start(self):
        """Begin a sweep (or, while one is running, leave single-step mode and carry on)."""
        self.single = False
        self._push_mode()
        self.apply_rate(self.rate)
        if self.eng.status() & ST_BUSY:
            if self.eng.held():
                self.eng.step()         # a stop hold needs one STEP; harmless otherwise
        else:
            self._last = None
            self.eng.start()

    def stop(self):
        self.eng.abort()

    def step(self):
        """One position: switches to single-step mode first, then releases the hold."""
        if not self.single:
            self.single = True
            self._push_mode()
        if not self.eng.status() & ST_BUSY:
            self._last = None
            self.eng.start()            # computes position 0 and holds
            return
        if self.eng.held():
            self.eng.step()

    def set_pause_on_stop(self, on: bool):
        self.pause_on_stop = on
        self._push_mode()

    # --- polling -------------------------------------------------------------------------------------
    def poll(self) -> Frame:
        e = self.eng
        now = self.clock()
        status = e.status()
        positions = e.rd(REG["POSITIONS"])
        stale = False
        try:
            snap = e.snapshot()
        except RuntimeError:
            snap, stale = None, True
        stops = []
        if status & ST_STOP:
            stops = e.pop_stops()
        if self._last is not None and now > self._last[0]:
            inst = max(0, positions - self._last[1]) / (now - self._last[0])
            self._smoothed += 0.4 * (inst - self._smoothed)
        else:
            self._smoothed = 0.0
        self._last = (now, positions)
        drums = decode_drums(e.rd(REG["DRUM"]))
        return Frame(status, drums, snap, positions, self._smoothed, stops, e.test_register(), stale, now)


def banner_text(snap: "Snapshot | None", menu: MenuView):
    """(text, is_stop) for the banner under the title."""
    if snap is None:
        return "READY", False
    drums = "".join(letter(d) for d in (snap.drums if menu.greek else snap.drums[1:]))
    if snap.is_stop:
        reg = snap.cables[menu.test_letter]
        cands = stecker_candidates(reg)
        who = " or ".join(cands) if len(cands) <= 3 else f"{len(cands)} letters"
        return f"STOP at {drums}   {letter(menu.test_letter)} is steckered to {who}", True
    return f"searching ... {drums}", False


# ---------------------------------------------------------------------------------------------------
# Tkinter view
# ---------------------------------------------------------------------------------------------------
BG, PANEL, OFF, AMBER, TEXT, DIM, RED, GREEN = "#0d1117", "#161b22", "#1c2330", "#ffb000", "#e6edf3", "#7d8590", "#ff4d4d", "#3fb950"


def _mix(c0: str, c1: str, t: float) -> str:
    a = [int(c0[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


class DemoApp:
    """Window: dials, menu graph, wire grid, lamps, controls.  Polling runs on a worker thread."""

    CELL = 20

    def __init__(self, ctrl: DemoController, title: str = "FPGA bombe", fullscreen: bool = False):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ctrl = tk, ctrl
        self.menu = ctrl.menu()
        self.root = tk.Tk()
        self.root.title(title)
        self.root.configure(bg=BG)
        if fullscreen:
            self.root.attributes("-fullscreen", True)
        self.cv = tk.Canvas(self.root, width=1280, height=700, bg=BG, highlightthickness=0)
        self.cv.pack(fill="both", expand=True)
        self.cmds: queue.Queue = queue.Queue()
        self.latest = Frame()
        self.lock = threading.Lock()
        self.stop_flag = False
        self.error = None
        self.fv = [0.0] * 4                     # animated dial values
        self.bright = [[0.0] * 26 for _ in range(26)]
        self.stop_log: list = []
        self.banner_until = 0.0
        self.banner = ("READY", False)
        self.frames_drawn = 0
        self._build_static()
        self._build_controls(ttk)
        self.thread = threading.Thread(target=self._worker, daemon=True)

    # ---- layout -------------------------------------------------------------------------------------
    def _build_static(self):
        cv, m = self.cv, self.menu
        cv.create_text(20, 22, anchor="w", fill=TEXT, font=("Segoe UI", 20, "bold"), text="FPGA BOMBE")
        cv.create_text(250, 24, anchor="w", fill=DIM, font=("Segoe UI", 11), text="same menu, same stops - no moving parts")
        self.banner_rect = cv.create_rectangle(20, 50, 1260, 100, fill=PANEL, outline="")
        self.banner_id = cv.create_text(640, 75, fill=TEXT, font=("Segoe UI", 22, "bold"), text="READY")
        # dials
        self.n_dials = 4 if m.greek else 3
        names = (["GREEK"] if m.greek else []) + ["LEFT", "MIDDLE", "RIGHT"]
        self.dial_x = [20 + i * 100 for i in range(self.n_dials)]
        for x, n in zip(self.dial_x, names):
            cv.create_rectangle(x, 120, x + 90, 330, fill=PANEL, outline=DIM)
            cv.create_text(x + 45, 132, text=n, fill=DIM, font=("Segoe UI", 9))
            cv.create_rectangle(x + 4, 205, x + 86, 245, outline=RED, width=2)
        self.dial_items = [[cv.create_text(x + 45, 225, text="", font=("Consolas", 22), fill=TEXT) for _ in range(5)]
                           for x in self.dial_x]
        # menu graph
        cv.create_text(20, 350, anchor="w", text="MENU", fill=DIM, font=("Segoe UI", 10))
        self.nodes = dict(zip(m.letters, circle_layout(len(m.letters), 215, 515, 135)))
        for a, b, k in m.links:
            (x0, y0), (x1, y1) = self.nodes[a], self.nodes[b]
            if a == b:
                cv.create_oval(x0 - 12, y0 - 38, x0 + 12, y0 - 14, outline=DIM)
                cv.create_text(x0, y0 - 46, text=str(k), fill=AMBER, font=("Segoe UI", 9))
                continue
            cv.create_line(x0, y0, x1, y1, fill=DIM)
            tx, ty = (x0 + x1) / 2, (y0 + y1) / 2
            cv.create_rectangle(tx - 9, ty - 8, tx + 9, ty + 8, fill=BG, outline="")
            cv.create_text(tx, ty, text=str(k), fill=AMBER, font=("Segoe UI", 9))
        for c, (x, y) in self.nodes.items():
            hot = c == m.test_letter
            cv.create_oval(x - 14, y - 14, x + 14, y + 14, fill=RED if hot else PANEL, outline=TEXT)
            cv.create_text(x, y, text=letter(c), fill=TEXT, font=("Consolas", 14, "bold"))
        # wire grid: row = cable (letter), column = wire
        self.gx, self.gy, cs = 470, 150, self.CELL
        cv.create_text(self.gx, 128, anchor="w", fill=DIM, font=("Segoe UI", 10), text="676 WIRES  (rows: cable, columns: wire)")
        for i in range(26):
            cv.create_text(self.gx + i * cs + cs / 2, self.gy - 8, text=letter(i), fill=DIM, font=("Consolas", 9))
            row_hot = i in m.letters
            cv.create_text(self.gx - 12, self.gy + i * cs + cs / 2, text=letter(i),
                           fill=RED if i == m.test_letter else (TEXT if row_hot else DIM), font=("Consolas", 9, "bold" if row_hot else "normal"))
        self.cells = [[cv.create_rectangle(self.gx + w * cs + 1, self.gy + c * cs + 1, self.gx + (w + 1) * cs - 1,
                                           self.gy + (c + 1) * cs - 1, fill=OFF, outline="") for w in range(26)] for c in range(26)]
        t = m.test_letter
        cv.create_rectangle(self.gx - 1, self.gy + t * cs - 1, self.gx + 26 * cs + 1, self.gy + (t + 1) * cs + 1, outline=RED, width=2)
        # right panel
        rx = 1030
        cv.create_text(rx, 128, anchor="w", fill=DIM, font=("Segoe UI", 10), text="TEST REGISTER  (cable %s)" % letter(t))
        self.lamps = []
        for i in range(26):
            x, y = rx + (i % 7) * 32 + 12, 160 + (i // 7) * 40
            self.lamps.append(cv.create_oval(x - 11, y - 11, x + 11, y + 11, fill=OFF, outline=DIM))
            cv.create_text(x, y, text=letter(i), fill=DIM, font=("Consolas", 9))
        cv.create_text(rx, 330, anchor="w", fill=DIM, font=("Segoe UI", 10), text="POSITIONS / SECOND")
        self.rate_id = cv.create_text(rx, 370, anchor="w", fill=AMBER, font=("Consolas", 28, "bold"), text="0")
        self.count_id = cv.create_text(rx, 410, anchor="w", fill=TEXT, font=("Consolas", 12), text="")
        self.bar = cv.create_rectangle(rx, 430, rx + 220, 442, fill=OFF, outline=DIM)
        self.bar_fill = cv.create_rectangle(rx, 430, rx, 442, fill=AMBER, outline="")
        self.iter_id = cv.create_text(rx, 462, anchor="w", fill=DIM, font=("Consolas", 10), text="")
        cv.create_text(rx, 500, anchor="w", fill=DIM, font=("Segoe UI", 10), text="STOPS FOUND")
        self.log_id = cv.create_text(rx, 515, anchor="nw", fill=TEXT, font=("Consolas", 10), text="")

    def _build_controls(self, ttk):
        tk = self.tk
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=20, pady=8)
        btn = dict(font=("Segoe UI", 12, "bold"), width=9)
        tk.Button(bar, text="Start / Run", command=lambda: self.cmds.put(self.ctrl.start), **btn).pack(side="left", padx=4)
        tk.Button(bar, text="Stop", command=lambda: self.cmds.put(self.ctrl.stop), **btn).pack(side="left", padx=4)
        tk.Button(bar, text="Step", command=lambda: self.cmds.put(self.ctrl.step), **btn).pack(side="left", padx=4)
        self.pause_var = tk.BooleanVar(value=self.ctrl.pause_on_stop)
        tk.Checkbutton(bar, text="pause on stop", variable=self.pause_var, bg=BG, fg=TEXT, selectcolor=PANEL, activebackground=BG,
                       command=lambda: self.cmds.put(lambda: self.ctrl.set_pause_on_stop(self.pause_var.get()))).pack(side="left", padx=12)
        tk.Label(bar, text="slow", bg=BG, fg=DIM).pack(side="left", padx=(20, 4))
        self.slider = tk.Scale(bar, from_=0, to=SLIDER_MAX, orient="horizontal", length=420, showvalue=False, bg=BG, fg=TEXT,
                               highlightthickness=0, troughcolor=PANEL, command=self._slider_moved)
        self.slider.set(rate_to_slider(self.ctrl.rate))
        self.slider.pack(side="left")
        tk.Label(bar, text="fast", bg=BG, fg=DIM).pack(side="left", padx=4)
        self.rate_label = tk.Label(bar, text="", bg=BG, fg=AMBER, font=("Consolas", 12), width=18, anchor="w")
        self.rate_label.pack(side="left", padx=8)
        self._slider_moved(self.slider.get())

    def _slider_moved(self, value):
        rate = slider_to_rate(float(value))
        self.rate_label.config(text="full speed" if rate is None else f"{rate:,.1f} pos/s")
        self.cmds.put(lambda r=rate: self.ctrl.apply_rate(r))

    # ---- polling thread -------------------------------------------------------------------------------
    def _worker(self):
        while not self.stop_flag:
            try:
                while True:
                    self.cmds.get_nowait()()
            except queue.Empty:
                pass
            except Exception as exc:                   # noqa: BLE001 - show, do not die silently
                self.error = exc
            try:
                f = self.ctrl.poll()
                with self.lock:
                    f.new_stops = self.latest.new_stops + f.new_stops
                    self.latest = f
            except Exception as exc:                   # noqa: BLE001
                self.error = exc
            time.sleep(0.02)

    # ---- drawing -----------------------------------------------------------------------------------------
    def _draw_dial(self, k: int, target: int):
        fv = self.fv[k]
        diff = (target - fv) % 26
        fv = float(target) if diff > 6 or diff == 0 else fv + max(0.12, diff * 0.3)
        fv %= 26
        self.fv[k] = fv
        base = math.floor(fv)
        frac = fv - base
        for j, item in enumerate(self.dial_items[k]):
            off = j - 2
            y = 225 + (off - frac) * 42
            d = abs(off - frac)
            self.cv.coords(item, self.dial_x[k] + 45, y)
            self.cv.itemconfig(item, text=letter(base + off), fill=_mix(TEXT, PANEL, min(1.0, d / 2.4)),
                               font=("Consolas", max(10, round(26 - 5 * d)), "bold"))

    def _tick(self):
        now = time.monotonic()
        with self.lock:
            f = self.latest
            self.latest = Frame(**{**f.__dict__, "new_stops": []})
        m, cv = self.menu, self.cv
        if self.error is not None:
            cv.itemconfig(self.banner_id, text=f"ERROR: {self.error}", fill=RED)
        else:
            snap = f.snap
            shown = (snap.drums if snap else f.drums)
            for k in range(self.n_dials):
                self._draw_dial(k, shown[k + (0 if m.greek else 1)])
            for drums, reg in f.new_stops:
                self.stop_log.append("".join(letter(d) for d in (drums if m.greek else drums[1:])) + " " + "/".join(stecker_candidates(reg))[:20])
            self.stop_log = self.stop_log[-12:]
            cv.itemconfig(self.log_id, text="\n".join(self.stop_log))
            text, is_stop = banner_text(snap, m)
            if snap and snap.is_stop and (f.held or any(True for _ in f.new_stops)):
                self.banner_until = now + 4.0
            if is_stop and (f.held or now < self.banner_until):
                self.banner = (text, True)
            elif not (self.banner[1] and now < self.banner_until):
                self.banner = (text, False)
            if f.done and not self.banner[1]:
                self.banner = ("SWEEP COMPLETE", False)
            cv.itemconfig(self.banner_id, text=self.banner[0], fill=RED if self.banner[1] else TEXT)
            cv.itemconfig(self.banner_rect, fill="#3a1116" if self.banner[1] else PANEL)
            # grid with afterglow
            cables = snap.cables if snap else (0,) * 26
            for c in range(26):
                for w in range(26):
                    b = 1.0 if cables[c] >> w & 1 else self.bright[c][w] * 0.75
                    if abs(b - self.bright[c][w]) > 0.01:
                        cv.itemconfig(self.cells[c][w], fill=_mix(OFF, AMBER, b))
                        self.bright[c][w] = b
            reg = cables[m.test_letter] if snap else 0
            for i, lamp in enumerate(self.lamps):
                cv.itemconfig(lamp, fill=AMBER if reg >> i & 1 else OFF)
            cv.itemconfig(self.rate_id, text=f"{f.rate:,.0f}" if f.rate >= 10 else f"{f.rate:.1f}")
            cv.itemconfig(self.count_id, text=f"{f.positions:,} of {m.total_positions:,}")
            cv.coords(self.bar_fill, 1030, 430, 1030 + 220 * min(1.0, f.positions / m.total_positions), 442)
            cv.itemconfig(self.iter_id, text=(f"{snap.iterations} propagation steps" + ("  (stale)" if f.stale else "")) if snap else "")
        self.frames_drawn += 1
        if self.max_frames is not None and self.frames_drawn >= self.max_frames:
            self.close()
            return
        self.root.after(33, self._tick)

    max_frames = None

    def close(self):
        self.stop_flag = True
        self.root.destroy()

    def run(self, max_frames=None):
        """Run the window until closed (or for ``max_frames`` frames: smoke test)."""
        self.max_frames = max_frames
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda _e: self.close())
        self.thread.start()
        self.root.after(100, self._tick)
        self.root.mainloop()
