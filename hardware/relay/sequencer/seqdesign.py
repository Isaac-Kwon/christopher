"""Design data of the relay machine's clock sequencer (decisions 0001 / 0003, README.md).

One list of parts with their nets (`build()`), used by gen_sequencer.py (schematic), gen_sequencer_pcb.py (placement) and
verify_sequencer.py (only for relay names / expected timing windows; the connectivity that is simulated comes from the
exported KiCad netlist).

Notation of the nets (all global, plain names):  SW = +24V_SW (cut by the emergency stop), V5 = 5 V logic, GND = 0 V.
Ring: Q0..Q9 = outputs of the ring counter (5 V).  Windows (diode-OR of ring outputs):
  WS = Q0|Q1 (STROBE)   WJ = Q3|Q4 (JUDGE)   WC = Q6|Q7|Q8 (CLEAR)   WK = Q7|Q8 (CK; also the HOME "done" window)
  HKON = Q5..Q8 (+ V5 through KHD once the HOME is done: counters' clock supply on)   HLD = Q3..Q6 (HOME: LOAD on)
"""
from __future__ import annotations

# ----------------------------------------------------------------------------- footprints / parts
FP = dict(
    relay="Relay_THT:Relay_DPDT_Omron_G6S-2",
    dsub37=("Connector_Dsub:DSUB-37_Pins_Horizontal_P2.77x2.84mm_EdgePinOffset9.90mm_Housed_MountingHolesOffset11.32mm"),
    dsub25=("Connector_Dsub:DSUB-25_Pins_Horizontal_P2.77x2.84mm_EdgePinOffset9.90mm_Housed_MountingHolesOffset11.32mm"),
    dsub9=("Connector_Dsub:DSUB-9_Pins_Horizontal_P2.77x2.84mm_EdgePinOffset9.90mm_Housed_MountingHolesOffset11.32mm"),
    d="Diode_SMD:D_SOD-123", tvs="Diode_SMD:D_SMA",
    r="Resistor_SMD:R_0603_1608Metric", c="Capacitor_SMD:C_0603_1608Metric", c8="Capacitor_SMD:C_0805_2012Metric",
    c12="Capacitor_SMD:C_1206_3216Metric", led="LED_SMD:LED_0603_1608Metric",
    cp="Capacitor_THT:CP_Radial_D10.0mm_P5.00mm", cp8="Capacitor_THT:CP_Radial_D8.0mm_P3.50mm",
    f1206="Fuse:Fuse_1206_3216Metric", f1812="Fuse:Fuse_1812_4532Metric", f2920="Fuse:Fuse_2920_7451Metric",
    t2="TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal",
    t4="TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-4-5.08_1x04_P5.08mm_Horizontal",
    sot223="Package_TO_SOT_SMD:SOT-223-3_TabPin2", so8="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    so16="Package_SO:SOIC-16_3.9x9.9mm_P1.27mm", so18w="Package_SO:SOIC-18W_7.5x11.6mm_P1.27mm",
    tsr="Converter_DCDC:Converter_DCDC_TRACO_TSR-1_THT",
    pot="Potentiometer_THT:Potentiometer_Bourns_3296W_Vertical",
    sjb="Jumper:SolderJumper-2_P1.3mm_Bridged_RoundedPad1.0x1.5mm", sjo="Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm",
    hole="MountingHole:MountingHole_3.2mm_M3_Pad")

T_NOM = 6.3              # ring tick, ms (555 astable, README 3)
R_SLOW = "680k"          # observation-mode resistor in series with R2 (README 2.1): T = 6.3 -> 27.0 ms, 270 ms / position
T_OBS = 27.0             # nominal observation tick, ms (0.693 (R1 + 2 (R2 + RV1 + R_SLOW)) C)
RING_WINDOWS = {         # ring state -> diode-OR window nets it feeds
    "WS": (0, 1), "WJ": (3, 4), "WC": (6, 7, 8), "WK": (7, 8), "HKON": (5, 6, 7, 8), "HLD": (3, 4, 5, 6)}

# relay table: name -> (description, fast_clamp?)   coil net and contacts are in build()
CTRL_PINS = {1: "STROBE", 2: "RUN", 3: "STOPPED", 4: "HOME", 5: "AT_HOME", 6: "END", 22: "ESTOP_A", 23: "ESTOP_RET",
             24: "ILOCK_A", 25: "ILOCK_RET", 34: "JUDGE", 35: "CLEAR", 36: "STOP_LATCH"}
for _k in range(15):
    CTRL_PINS[7 + _k] = f"POS{_k}"
CTRL_GND = tuple(range(28, 34))
CTRL_NC = (26, 27, 37)


class Part:
    def __init__(self, sheet, kind, ref, value, fp, nets, desc="", nc=(), unit=1, ds="", bom=True):
        self.sheet, self.kind, self.ref, self.value, self.fp = sheet, kind, ref, value, fp
        self.nets, self.desc, self.nc, self.unit, self.ds, self.bom = nets, desc, tuple(nc), unit, ds, bom


class Design:
    def __init__(self):
        self.parts: list[Part] = []
        self.cnt: dict[str, int] = {}
        self.relays: dict[str, str] = {}          # name -> ref
        self.clamped: set[str] = set()

    def ref(self, prefix):
        self.cnt[prefix] = self.cnt.get(prefix, 0) + 1
        return f"{prefix}{self.cnt[prefix]}"

    def add(self, sheet, kind, prefix, value, fp, nets, desc="", nc=(), unit=1, ds="", bom=True, ref=None):
        ref = ref or self.ref(prefix)
        self.parts.append(Part(sheet, kind, ref, value, fp, nets, desc, nc, unit, ds, bom))
        return ref

    # -- passives
    def R(self, sheet, a, b, val, desc=""):
        return self.add(sheet, "R", "R", val, FP["r"], {"1": a, "2": b}, desc or f"Resistor {val}")

    def C(self, sheet, a, b, val, desc="", fp=None):
        return self.add(sheet, "C", "C", val, fp or FP["c"], {"1": a, "2": b}, desc or f"Capacitor {val}")

    def D(self, sheet, anode, cathode, val="1N4148W", desc="", kind="D"):
        """Device:D: pin 1 = cathode, pin 2 = anode."""
        return self.add(sheet, kind, "D", val, FP["d"], {"1": cathode, "2": anode}, desc or f"Diode {val}")

    def DS(self, sheet, anode, cathode, desc=""):
        return self.add(sheet, "DS", "D", "BAT54 / SS14 Schottky", FP["d"], {"1": cathode, "2": anode}, desc or "Schottky diode")

    def DZ(self, sheet, anode, cathode, val, desc=""):
        return self.add(sheet, "DZ", "DZ", val, FP["d"], {"1": cathode, "2": anode}, desc or f"Zener {val}")

    def LED(self, sheet, net, desc, res="4.7k", rail=None):
        """LED + series resistor from `net` to 0 V."""
        n = f"LED_{self.cnt.get('LEDN', 0) + 1}"
        self.cnt["LEDN"] = self.cnt.get("LEDN", 0) + 1
        self.add(sheet, "R", "R", res, FP["r"], {"1": net, "2": n}, f"{desc}: indicator resistor")
        self.add(sheet, "LED", "DL", "LED", FP["led"], {"1": "GND", "2": n}, f"{desc}: indicator LED")

    def PTC(self, sheet, a, b, val, fp, desc=""):
        return self.add(sheet, "F", "F", val, fp, {"1": a, "2": b}, desc or f"Resettable fuse {val}")

    # -- relays
    def relay(self, sheet, name, coil, p1=None, p2=None, fast=False, desc="", led=None, clamp=True, series=None):
        """p1/p2 = (com, nc, no) net names (None = unused).  The clamp (flyback diode, with a 30 V zener for the fast ones)
        is made once per coil net.  `series` = (net_from, ...): handled by the caller."""
        ref = self.ref("K")
        nets = {"1": coil, "12": "GND"}
        for pole, pins in ((p1, ("4", "3", "5")), (p2, ("9", "10", "8"))):
            if pole:
                for net, pin in zip(pole, pins):
                    if net:
                        nets[pin] = net
        nc = [p for p in ("1", "3", "4", "5", "8", "9", "10", "12") if p not in nets]
        self.add(sheet, "K", "K", f"G6S-2 {name}", FP["relay"], nets, f"{name}: {desc}", ref=ref, nc=nc,
                 ds="http://omronfs.omron.com/en_US/ecb/products/pdf/en-g6s.pdf")
        self.relays[name] = ref
        if clamp and coil not in self.clamped:
            self.clamped.add(coil)
            if fast:
                x = f"X_{coil}"
                self.D(sheet, "GND", x, "1N4148W", f"{coil}: coil clamp diode (with the zener: fast release)")
                self.DZ(sheet, coil, x, "BZT52C30", f"{coil}: coil clamp zener 30 V")
            else:
                self.D(sheet, "GND", coil, "1N4148W", f"{coil}: coil flyback diode")
        if led:
            self.LED(sheet, coil, led)
        return ref


def build() -> Design:
    d = Design()
    S = d.add

    # =========================================================================== POWER
    sp = "power"
    S(sp, "T2", "J", "24V_SW_IN", FP["t2"], {"1": "VIN", "2": "GND"}, "Power in (decision 0003 5.2): 1 = +24V_SW (cut by the emergency stop, power-unit F38 3.15 A), 2 = 0 V")
    d.PTC(sp, "VIN", "VF", "PTC 3A hold", FP["f2920"], "Board fuse (resettable); the CK/NCK/LOAD rails of both buses pass through it (about 1.3 A at 12 units)")
    S(sp, "QP", "Q", "P-MOSFET (reverse polarity)", FP["sot223"], {"1": "GATEP", "2": "VF", "3": "SW"},
      "Series P-MOSFET (0003 7.3): drain = supply side, source = +24V_SW rail")
    d.DZ(sp, "GATEP", "SW", "12V", "Gate-source zener 12 V (anode on the gate)")
    d.R(sp, "GATEP", "GND", "100k", "P-MOSFET gate pull-down")
    S(sp, "TVS", "D", "SMAJ33A", FP["tvs"], {"1": "SW", "2": "GND"}, "TVS 33 V behind the series MOSFET")
    d.add(sp, "CP", "C", "470u 35V", FP["cp"], {"1": "SW", "2": "GND"}, "Bulk capacitor (rail steps of the CK/NCK relay contacts)")
    d.C(sp, "SW", "GND", "100n 50V", "HF capacitor", FP["c8"])
    S(sp, "TSR", "U", "TSR 1-2450", FP["tsr"], {"1": "SW", "2": "GND", "3": "V5"}, "Traco TSR 1-2450: +24V_SW -> 5 V 1 A (clock, ring counter, driver inputs)",
      ds="https://www.tracopower.com/products/tsr1.pdf", ref="U80")
    d.C(sp, "V5", "GND", "22u 10V", "5 V bulk", FP["c12"])
    d.C(sp, "V5", "GND", "100n", "5 V HF", FP["c"])
    d.R(sp, "SW", "LEDSW", "4.7k", "+24V_SW indicator resistor")
    S(sp, "LED", "DL", "+24V_SW", FP["led"], {"1": "GND", "2": "LEDSW"}, "+24V_SW present")
    for k in range(4):
        S(sp, "HOLE", "H", "MountingHole_Pad", FP["hole"], {"1": "CHASSIS"}, "Plated M3 mounting hole, net CHASSIS", bom=False)
    d.R(sp, "CHASSIS", "GND", "1M", "CHASSIS to 0 V: static drain")
    d.C(sp, "CHASSIS", "GND", "10n 100V", "CHASSIS to 0 V: ESD / HF path")

    # =========================================================================== TIME BASE: 555 + ring counter
    st = "timebase"
    S(st, "555", "U", "LMC555", FP["so8"],
      {"1": "GND", "8": "V5", "2": "TH", "6": "TH", "7": "DIS", "3": "CLK", "4": "V5", "5": "CTL"},
      "CMOS 555 astable: tick period T = 0.693 (R1 + 2 (R2 + RV1)) C = 6.3 ms (adjustable 4.7-7.8 ms); observation mode "
      "(R_SLOW in series with R2) 27 ms", ref="U1")
    d.R(st, "V5", "DIS", "10k", "555 R1")
    d.R(st, "DIS", "RSL", R_SLOW, "555 observation-mode resistor in series with R2 (shorted by the KOBS / KSCb NC contacts in fast "
        "mode).  Only a resistance changes, never the capacitor: the capacitor voltage is continuous, so a switch at any instant "
        "can only lengthen the running tick, never shorten it below the fast tick")
    d.R(st, "RSL", "RM", "150k", "555 R2 (fixed part)")
    S(st, "POT", "RV", "100k trim", FP["pot"], {"1": "RM", "2": "TH", "3": "TH"}, "Tick trimmer: R2 = 150k + RV1 (set T = 6.3 ms with an oscilloscope)")
    d.C(st, "TH", "GND", "22n C0G", "Timing capacitor (NP0 / C0G: tick tolerance 3 %)")
    d.C(st, "CTL", "GND", "10n", "555 control pin capacitor")
    d.C(st, "V5", "GND", "100n", "555 supply")
    ring = {"3": "Q0", "2": "Q1", "4": "Q2", "7": "Q3", "10": "Q4", "1": "Q5", "5": "Q6", "6": "Q7", "9": "Q8", "11": "Q9",
            "8": "GND", "16": "V5", "14": "CLK", "13": "PARK", "15": "RING_RST"}
    S(st, "4017", "U", "74HC4017", FP["so16"], {**ring, "12": "RING_CO"}, "Decade ring counter: Q0..Q9 one-hot; pin 13 (clock inhibit) = PARK (ring stops in Q9 when no cycle is wanted)",
      nc=("12",), ref="U2")
    d.C(st, "V5", "GND", "100n", "4017 supply")
    d.C(st, "V5", "RING_RST", "100n", "Power-on reset pulse of the ring")
    d.R(st, "RING_RST", "GND", "100k", "Ring reset pull-down")
    for q in range(10):
        d.LED(st, f"Q{q}", f"Ring state Q{q}", res="1k")
    # diode-OR windows
    for w, qs in RING_WINDOWS.items():
        for q in qs:
            d.D(st, f"Q{q}", w, "1N4148W", f"Window {w}: ring state Q{q}")
        d.R(st, w, "GND", "47k", f"Window {w} pull-down")

    # TBD62783A drivers (24 V high-side) for the relays that the ring drives
    sd = "drivers"
    tbd1 = {"1": "INS", "2": "INJ", "3": "INC", "4": "CKIN", "5": "KENIN", "6": "LDIN", "7": "DONEIN", "8": "ADVIN",
            "18": "S_OUT", "17": "J_OUT", "16": "C_OUT", "15": "CK_OUT", "14": "EN_OUT", "13": "LD_OUT", "12": "DONE_OUT",
            "11": "ADV_OUT", "9": "SW", "10": "GND"}
    S(sd, "TBD", "U", "TBD62783A", FP["so18w"], tbd1, "High-side driver 1: STROBE, JUDGE, CLEAR, CK, KEN, LOAD, DONE, ADV relays", ref="U3")
    tbd2 = {"1": "PARK", "2": "KHIN", "3": "SCIN", "4": "GND", "5": "GND", "6": "GND", "7": "GND", "8": "GND", "9": "SW", "10": "GND",
            "18": "ST_OUT", "17": "KH_OUT", "16": "SC_OUT"}
    S(sd, "TBD", "U", "TBD62783A", FP["so18w"], tbd2, "High-side driver 2: STOPPED relay (channel 1), CK decision relay KCKH (channel 2), "
      "step-cycle relay KSC (channel 3, set in the STROBE window of an armed step); the other channels unused", ref="U4",
      nc=("15", "14", "13", "12", "11"))
    d.C(sd, "SW", "GND", "100n 50V", "Driver supply decoupling", FP["c8"])
    d.C(sd, "SW", "GND", "100n 50V", "Driver supply decoupling", FP["c8"])
    for n in ("INS", "INJ", "INC", "CKIN", "KENIN", "LDIN", "DONEIN", "ADVIN", "PARK", "KHIN", "SCIN"):
        d.R(sd, n, "GND", "47k", f"{n} driver-input pull-down (defined low while the contact is open)")

    # =========================================================================== INPUT FOLLOWER RELAYS
    si = "inputs"
    for name, pin, coilnet, desc in (("KR", "RUN", "RUN_C", "RUN follower"), ("KSL", "STOP_LATCH", "SL_C", "STOP_LATCH follower"),
                                     ("KHM", "HOME", "HM_C", "HOME follower"), ("KP0", "POS10_IN", "P0_C", "POS10 (r bit 0) follower"),
                                     ("KP3", "POS13_IN", "P3_C", "POS13 (r bit 3) follower"), ("KP4", "POS14_IN", "P4_C", "POS14 (r bit 4) follower"),
                                     ("KOBS", "SPEED_OBS", "OBS_C", "speed selector (observation) follower"),
                                     ("KSP", "STEP", "SP_C", "single-step button follower")):
        # decision 0003 2.2: pin -> series Schottky diode -> coil, clamp on the coil side, pull-down + LED on the pin side
        d.R(si, pin, "GND", "22k", f"{pin} pin pull-down")
        d.R(si, pin, f"LP_{name}", "6.8k", f"{pin} indicator resistor")
        d.add(si, "LED", "DL", "LED", FP["led"], {"1": "GND", "2": f"LP_{name}"}, f"{pin} indicator LED")
        d.DS(si, pin, coilnet, f"{name}: series Schottky (pin -> coil); the clamp is on the coil side")
    d.relay(si, "KR", "RUN_C", ("SW", "RN", "KRX_C"), ("Q6", None, "KHS"), fast=True,
            desc="RUN follower. pole 1: SW -> KRX coil (NO), SW -> RN = RUN low (NC: feeds the step latch holds, so RUN dominates a "
                 "step); pole 2: ring state Q6 -> sample drive of the CK decision relay KCKH while RUN is high")
    d.relay(si, "KSL", "SL_C", ("SW", "GA1", None), ("Q6", "Q6S", None), fast=True,
            desc="STOP_LATCH follower. pole 1 NC: SW -> GA1 (head of the GO chain and the quiet chain, both through KRX); "
                 "pole 2 NC: Q6 -> Q6S = sample of the step-cycle CK decision (a step never advances past a register stop)")
    d.relay(si, "KOBS", "OBS_C", ("DIS", "RSM", None), led=None,
            desc="Speed selector follower (observation).  pole 1 NC: shorts the observation resistor of the 555 (with KSCb) in fast mode")
    d.relay(si, "KSP", "SP_C", ("SAP", None, "SAS"), ("SAH1", None, "SA_C"),
            desc="STEP button follower.  Plain flyback clamp (release 3-10 ms): contact bounce shorter than that never releases it.  "
                 "pole 1: step arm (parked -> KSA coil); pole 2: KSA hold while the button is held (no repeat)")
    d.relay(si, "KHM", "HM_C", ("SW", None, "HA1"), ("SW", "SBH1", None), fast=True, desc="HOME follower. NO: HOME accept chain; NC: standby hold")
    d.relay(si, "KP0", "P0_C", ("SW", None, "E1"), fast=True, desc="r bit 0 follower: r = 25 chain contact (SW -> E1)")
    d.relay(si, "KP3", "P3_C", ("E1", None, "E2"), fast=True, desc="r bit 3 follower: r = 25 chain contact (E1 -> E2)")
    d.relay(si, "KP4", "P4_C", ("E2", None, "R25_C"), fast=True, desc="r bit 4 follower: r = 25 chain contact (E2 -> KR25 coil)")
    d.relay(si, "KHT", "HT_C", ("GA4", "GA6", None), ("SW", None, "HOTS1"), fast=False, desc="Thermostat follower: NC in the GO chain; NO = hot -> standby request", led="HOT (thermostat)")

    # =========================================================================== STATE RELAYS (24 V logic)
    sl = "logic"
    d.relay(sl, "KR25", "R25_C", ("A1B", None, "ENDSET"), led="r = 25", desc="r = 25 decoded from POS10 / POS13 / POS14 (static, before the last CK).  Contact: m carry A1B -> END set.  Its release delay keeps the chain closed until the END relay has operated")
    d.relay(sl, "KRX", "KRX_C", ("GA1", "KQ_C", "GA2"), desc="RUN secondary (contact multiplier).  COM = GA1 (after the STOP_LATCH NC): NO -> GO chain, NC -> quiet coil")
    d.relay(sl, "KQ", "KQ_C", ("HA1", None, "HA2"), ("ID1", None, "TMRC"), desc="Quiet = RUN low and STOP_LATCH low. HOME accept chain, idle chain")
    # GO relay: ring runs (leaves Q9) while GO
    d.D(sl, "GA6", "GO_C", "1N4148W", "GO chain A diode (OR)")
    d.D(sl, "GB4", "GO_C", "1N4148W", "GO chain B (HOME program) diode (OR)")
    d.relay(sl, "KGO", "GO_C", ("Q9", "PARK", None), ("SAH1", None, "SA_C"), fast=True, led="GO (ring runs)",
            desc="GO. NC contact: Q9 -> PARK (clock inhibit) unless GO.  Chain A: (RUN or an armed step) & not STOP_LATCH & cycle enable (KENK) & not END & not hot.  Chain B: HOMING and not at home.  p2: holds an armed step (KSA) once the ring has been released, so a short tap is not lost")
    d.relay(sl, "KRDY", "RDY_C", ("HD1", None, "RDY_C"), led="READY",
            desc="READY: counters hold a valid position (set by HOME done; cleared by standby).  p1 hold")
    d.relay(sl, "KRDYb", "RDY_C", ("SW", None, "KE1"), ("V5", None, "KEN1"),
            desc="READY twin: enables the cycle enable chain (KENK); p2 supplies the counters' clock drive (KEN) while READY")
    d.relay(sl, "KSB", "SB_C", ("SBH1", None, "SB_C"), ("SW", "HD1", None), led="STANDBY",
            desc="STANDBY (idle timer / thermostat): coils dropped (CK = NCK = 0).  Cleared by HOME.  p2 breaks the READY hold")
    d.relay(sl, "KSBb", "SB_C", ("KCKNC", "KATH1", None), desc="STANDBY twin: breaks the AT_HOME hold")
    d.relay(sl, "KAT", "AT_C", ("KATH1", None, "AT_C"), ("SW", None, "ATH_OUT"), led="AT HOME",
            desc="AT_HOME latch: set by HOME done, cleared at the first CK (KCKA pole 2) or standby.  p2 = AT_HOME output")
    d.relay(sl, "KATb", "AT_C", ("HA2", "HA3", None), ("SW", "HE1", None), desc="AT_HOME twin: HOME accept chain (not at home); END hold (cleared when HOME is done)")
    d.relay(sl, "KATc", "AT_C", ("GB", "GB2", None), ("SW", "HLH1", None), desc="AT_HOME twin: GO chain B and HOMING hold are cut when HOME is done")
    d.relay(sl, "KEND", "END_C", ("HE1", None, "END_C"), ("SW", None, "END_OUT"), led="END", desc="END latch (set by the m carry A1B and r = 25, or by A1B alone in slice mode).  p2 = END output")
    d.relay(sl, "KENDb", "END_C", ("GA4a", "GA4", None), desc="END twin: GO chain (no run while END)")
    d.relay(sl, "KADV", "ADV_C", ("KADVP", None, "KADVH"), ("EW1", "EW2", None), fast=True, led="ADVANCE pending",
            desc="Advance pending: CK was blocked (RUN low) in the last cycle.  Cleared when a CK is done.  p2: STROBE/JUDGE gating chain")
    d.relay(sl, "KADVb", "ADV_C", ("GA3y", None, "GA4a"), desc="Advance pending twin: GO chain A may start an advance-only cycle")
    d.D(sl, "KADVH", "ADV_C", "1N4148W", "KADV hold feed diode (keeps the fast clamp isolated from the hold contact)")
    d.relay(sl, "KHL", "HL_C", ("HLH1", None, "HL_C"), ("KENIN", "KEN1", "HKON"), led="HOMING",
            desc="HOMING (accepted HOME request).  p1 hold; p2: counters' clock supply drive = READY level normally, window HKON during the HOME program")
    d.relay(sl, "KHLb", "HL_C", ("SW", None, "GB"), ("KCKNC", "KADVP", None), desc="HOMING twin: GO chain B (runs the ring for the HOME program); p2 cuts the advance-pending hold (HOME resets the position)")
    d.relay(sl, "KHLc", "HL_C", ("HLD", None, "LDIN"), ("WK", None, "DONEIN"), desc="HOMING twin: LOAD window and DONE window gating")
    d.relay(sl, "KHLd", "HL_C", ("KE1", "KE2", None), ("WC", None, "INC"),
            desc="HOMING twin: cycle enable chain is off while HOMING; p2 = flush CLEAR of the HOME pass (window WC -> CLEAR drive "
                 "while HOMING; the CLEAR relay's own pole 2 holds it to the end of the window).  Decision 0003 4.4: HOME ends with "
                 "exactly one CLEAR (no STROBE / JUDGE) so that sense relays left latched by an emergency stop are released")
    d.relay(sl, "KHD", "HD_C", ("HDH1", None, "HD_C"), ("V5", None, "HKON"), led="HOME done pass",
            desc="HOME done pass: set by the DONE drive, cleared when the ring is parked.  Keeps the cycle windows closed for the rest of the HOME pass")
    d.relay(sl, "KHDb", "HD_C", ("KE2", "KE_C", None), desc="HOME done twin: cycle enable chain is off during the done pass")
    d.relay(sl, "KST", "ST_OUT", ("SW", None, "STP_OUT"), ("HOTS1", None, "HOTS2"), led="STOPPED (parked)",
            desc="Parked (ring in Q9, no GO): STOPPED output; thermostat standby only when parked")
    d.relay(sl, "KSTb", "ST_OUT", ("HA3", None, "HA4"), ("SW", None, "ID1"), desc="Parked twin: HOME accepted only when parked; idle timer only when parked")
    d.relay(sl, "KSTc", "ST_OUT", ("SW", "HDH1", "SAP"), ("SCH1", "SC_C", None),
            desc="Parked twin: p1 NC ends the HOME done pass (KHD hold is cut when the ring is parked), p1 NO: a step is accepted only when parked; p2 NC: holds the step-cycle relay KSC while the ring runs")
    d.relay(sl, "KENK", "KE_C", ("WK", None, "WKE"), ("WC", None, "INC"), desc="Enable chain: READY and not HOMING. CK window and CLEAR window gating")
    d.relay(sl, "KENKb", "KE_C", ("SW", None, "EW1"), desc="Enable twin: feeds the STROBE/JUDGE enable chain")
    d.relay(sl, "KENKc", "KE_C", ("GA2", None, "GA3"), ("GB2", "GB3", None), desc="Enable twin: GO chain A starts a run cycle only when the cycle windows are enabled; chain B (HOME) waits until KENK has released")
    d.D(sl, "EW2", "EW_C", "1N4148W", "KENW: series diode (chain -> coil; keeps the fast clamp isolated)")
    d.relay(sl, "KENW", "EW_C", ("WS", None, "INS"), ("WJ", None, "INJ"), fast=True, desc="STROBE/JUDGE window gating (READY, not HOMING, no advance pending)")
    d.relay(sl, "KENWb", "EW_C", ("GA3", "GA3y", "GA4a"), ("GB3", "GB4", None),
            desc="KENW twin: GO chain A starts a cycle only when KENW is operated (or an advance-only cycle with KENW released); chain B (HOME) waits until KENW has released")
    # --- single step (offset-dial README 5, decision 0003 4.2): one press = one ring revolution = one position cycle
    #   KSA  armed:      set = parked (KSTc) & button (KSP);  hold = RUN low (RN) & (button held | ring released (KGO p2))
    #   KSC  step cycle: set = STROBE window WS & armed (TBD channel 3);  hold = RUN low & (ring not parked | still armed)
    #   GO   step:       GA1 (= not STOP_LATCH) -> KSA -> KSC NC -> GA2 (rest of chain A: enable, advance-pending, END, hot)
    #   CK   step:       Q6 -> KSL NC (not STOP_LATCH) -> KSCb NO -> KHS (the CK decision that RUN gives in a run cycle)
    #   speed            KSCb p2 NC opens the 555 short: a step cycle always runs at observation speed
    d.D(sl, "SAS", "SA_C", "1N4148W", "Step arm (parked & button) -> KSA coil")
    d.relay(sl, "KSA", "SA_C", ("RN", None, "SAH1"), ("GA1", None, "GS1"),
            desc="Step ARMED.  p1: hold (RUN low, then the button KSP or the released ring KGO); p2: GO chain A entry of the step (GA1 -> GS1 -> KSC NC -> GA2)")
    d.relay(sl, "KSAb", "SA_C", ("WS", None, "SCIN"), ("SCH1", None, "SC_C"),
            desc="Step armed twin: p1 sets the step-cycle relay KSC in the STROBE window (WS -> driver channel); p2 keeps KSC while the button is held after the cycle (no repeat)")
    d.D(sl, "SC_OUT", "SC_C", "1N4148W", "KSC: series diode (driver -> coil)")
    d.relay(sl, "KSC", "SC_C", ("RN", None, "SCH1"), ("GS1", "GA2", None), led="STEP cycle",
            desc="STEP CYCLE: set when the ring has left Q9 for an armed step, held until the ring parks again and the button is released.  p1 hold; p2 NC cuts the step GO as soon as the cycle has started (exactly one cycle per press)")
    d.relay(sl, "KSCb", "SC_C", ("Q6S", None, "KHS"), ("RSM", "RSL", None),
            desc="Step cycle twin: p1 = CK decision of a step cycle (Q6 & not STOP_LATCH -> KCKH sample); p2 NC opens the 555 short: a step cycle runs at observation speed")
    # coil feeds of the chain-driven relays
    d.D(sl, "HA4", "HL_C", "1N4148W", "HOME accept -> HOMING coil (OR with the hold)")
    d.D(sl, "DONE_OUT", "RDY_C", "1N4148W", "HOME done -> READY coil")
    d.D(sl, "DONE_OUT", "AT_C", "1N4148W", "HOME done -> AT_HOME coil")
    d.D(sl, "DONE_OUT", "HD_C", "1N4148W", "HOME done -> done-pass coil")
    d.D(sl, "ADV_OUT", "ADV_C", "1N4148W", "ADV set drive -> ADVANCE coil")
    d.D(sl, "ENDSET", "END_C", "1N4148W", "END set (carry chain) -> END coil")
    d.D(sl, "TMR_RET", "SB_C", "1N4148W", "Idle timer expired -> STANDBY coil")
    d.D(sl, "HOTS2", "SB_C", "1N4148W", "Thermostat (hot, parked) -> STANDBY coil")
    S(sl, "SJO", "SJ", "slice END (open)", FP["sjo"], {"1": "A1B", "2": "ENDSET"},
      "Solder jumper, default OPEN: END at every m carry (slice of 676 positions) instead of the full run (r = 25)", bom=False)
    # coil of KENW is EW2: chain SW -> KENKb -> KADV.nc -> coil.  (EW2 is its coil net)
    # --- output relays driven by the ring
    so = "outputs"
    for name, drv, coil, desc in (("KS", "S_OUT", "KS_C", "STROBE relay"), ("KJ", "J_OUT", "KJ_C", "JUDGE relay"), ("KC", "C_OUT", "KC_C", "CLEAR relay")):
        d.D(so, drv, coil, "1N4148W", f"{name}: series diode (driver -> coil, 0003 R1 style)")
        p2 = ("WC", None, "INC") if name == "KC" else None     # CLEAR self-hold through its window (flush CLEAR of HOME)
        d.relay(so, name, coil, ("SW", None, {"KS": "STROBE_O", "KJ": "JUDGE_O", "KC": "CLEAR_O"}[name]), p2, fast=True,
                desc=desc + ("; p2 holds the CLEAR drive to the end of the WC window once it has started" if name == "KC" else ""))
    d.D(so, "CK_OUT", "CK_C", "1N4148W", "KCKA/KCKB: series diode (driver -> coils)")
    d.relay(so, "KCKA", "CK_C", ("KENO_A", "NCK_A", "CK_A"), ("SW", "KCKNC", None), fast=True,
            desc="CK/NCK changeover, bus A (counter common board + units 1-6). ONE changeover contact = break-before-make. Pole 2: AT_HOME / ADV hold break")
    d.relay(so, "KCKB", "CK_C", ("KENO_B", "NCK_B", "CK_B"), fast=True, desc="CK/NCK changeover, bus B (units 7-12), same drive")
    d.D(so, "KH_OUT", "KH_C", "1N4148W", "KCKH: series diode (driver -> coil)")
    d.relay(so, "KCKH", "KH_C", ("WC", None, "KHHOLD"), ("WKE", "ADVIN", "CKIN"), fast=True,
            desc="CK decision relay: sampled in ring state Q6 (RUN high), then self-held through Q7-Q8 (WC window) so that the decision cannot change while the CK pulse runs.  Pole 2 (one changeover): WKE -> CK drive (operated) or -> advance-pending set (released)")
    d.D(so, "KHS", "KHIN", "1N4148W", "KCKH sample feed (Q6 and RUN high)")
    d.D(so, "KHHOLD", "KHIN", "1N4148W", "KCKH hold feed (WC window through its own contact)")
    d.D(so, "EN_OUT", "EN_C", "1N4148W", "KEN: series diode (driver -> coil)")
    d.relay(so, "KEN", "EN_C", ("SW", None, "KENA_P"), ("SW", None, "KENB_P"), fast=True, desc="Counters' clock supply gate (CK = NCK = 0 when released): HOME, standby")
    d.D(so, "LD_OUT", "LD_C", "1N4148W", "KLD: series diode (driver -> coil)")
    d.relay(so, "KLD", "LD_C", ("SW", None, "LOADA_P"), ("SW", None, "LOADB_P"), fast=True, desc="LOAD (dial common) both buses")
    d.PTC(so, "KENA_P", "KENO_A", "PTC 1.1A hold", FP["f1812"], "Bus A clock-rail fuse (0.65 A at 12 units)")
    d.PTC(so, "KENB_P", "KENO_B", "PTC 1.1A hold", FP["f1812"], "Bus B clock-rail fuse")
    d.PTC(so, "LOADA_P", "LOAD_A", "PTC 0.1A hold", FP["f1206"], "LOAD bus A fuse")
    d.PTC(so, "LOADB_P", "LOAD_B", "PTC 0.1A hold", FP["f1206"], "LOAD bus B fuse")
    for net, pin, name in (("STROBE_O", "STROBE", "STROBE"), ("JUDGE_O", "JUDGE", "JUDGE"), ("CLEAR_O", "CLEAR", "CLEAR"),
                           ("STP_OUT", "STOPPED", "STOPPED"), ("ATH_OUT", "AT_HOME", "AT_HOME"), ("END_OUT", "END", "END")):
        d.PTC(so, net, pin, "PTC 30mA hold / 100mA trip", FP["f1206"], f"{name} output resettable fuse (hold >= 30 mA, trip <= 100 mA; part not confirmed)")
    for k in range(15):
        d.PTC(so, f"POS{k}_IN", f"POS{k}", "PTC 30mA hold / 100mA trip", FP["f1206"], f"POS{k} pass-through resettable fuse")

    # =========================================================================== CONNECTORS
    sc = "connectors"
    nets37 = {str(p): n for p, n in CTRL_PINS.items()}
    nets37.update({str(p): "GND" for p in CTRL_GND})
    S(sc, "DC37", "J", "CTRL", FP["dsub37"], nets37, "Control connector J2 (decision 0003 5.2): 1 STROBE, 2 RUN, 3 STOPPED, 4 HOME, 5 AT_HOME, 6 END, 7-21 POS0-14, 22/23 ESTOP loop, 24/25 ILOCK loop, 28-33 0 V, 34 JUDGE, 35 CLEAR, 36 STOP_LATCH",
      nc=[str(p) for p in CTRL_NC], ref="J2")
    S(sc, "DE9", "J", "CLK_A", FP["dsub9"], {"1": "CK_A", "2": "NCK_A", "3": "LOAD_A", "6": "GND", "7": "GND"},
      "CLOCK bus A (counter common board J1: 1 CK, 2 NCK, 3 LOAD, 6-7 0 V; units 1-6)", nc=("4", "5", "8", "9"), ref="J3")
    S(sc, "DE9", "J", "CLK_B", FP["dsub9"], {"1": "CK_B", "2": "NCK_B", "3": "LOAD_B", "6": "GND", "7": "GND"},
      "CLOCK bus B (units 7-12): same pin layout as bus A", nc=("4", "5", "8", "9"), ref="J4")
    n25 = {str(k + 1): f"POS{k}_IN" for k in range(15)}
    n25.update({"19": "GND", "20": "GND", "21": "GND"})
    S(sc, "DB25", "J", "POS_IN", FP["dsub25"], n25, "POS from the counter common board J4 (1-15 POS0-14, 19-21 0 V); the board passes them through fuses to J2 7-21", nc=("16", "17", "18", "22", "23", "24", "25"), ref="J5")
    S(sc, "DB25", "J", "BUS_TAP", FP["dsub25"], {"3": "A1B", "19": "GND", "20": "GND", "21": "GND"},
      "UNIT_BUS ribbon drop (counter-board J2 layout): only pin 3 (A1B = m carry rail) and the 0 V pins 19-21 are used",
      nc=tuple(str(p) for p in range(1, 26) if p not in (3, 19, 20, 21)), ref="J6")
    S(sc, "T4", "J", "SAFE", FP["t4"], {"1": "ESTOP_SRC", "2": "ESTOP_RET", "3": "ILOCK_SRC", "4": "ILOCK_RET"},
      "Bank-owned safety loops (0003 4.6): 1/2 ESTOP source (power unit F18) / return to K_E; 3/4 ILOCK source (F19) / return to K_I", ref="J7")
    S(sc, "SJB", "SJ", "ESTOP loop (bridged)", FP["sjb"], {"1": "ESTOP_SRC", "2": "ESTOP_A"},
      "ESTOP: bank-side emergency-stop switch place; closed by default", bom=False)
    S(sc, "SJB", "SJ", "ILOCK loop (bridged)", FP["sjb"], {"1": "ILOCK_SRC", "2": "ILOCK_A"},
      "ILOCK: service-cover switch place (relay machine has no moving parts: closed)", bom=False)
    d.R(sc, "ESTOP_RET", "LEDE", "4.7k", "ESTOP loop lamp resistor")
    S(sc, "LED", "DL", "ESTOP loop closed", FP["led"], {"1": "GND", "2": "LEDE"}, "ESTOP loop closed")
    d.R(sc, "ILOCK_RET", "LEDI", "4.7k", "ILOCK loop lamp resistor")
    S(sc, "LED", "DL", "ILOCK loop closed", FP["led"], {"1": "GND", "2": "LEDI"}, "ILOCK loop closed")
    S(sc, "T4", "J", "TIMER", FP["t4"], {"1": "TMRC", "2": "GND", "3": "SW", "4": "TMR_RET"},
      "Idle timer (external electromechanical on-delay relay, 24 V coil, 2 min): 1 = coil + (energised only when idle), 2 = 0 V, 3 = contact common (+24V_SW), 4 = contact NO (returns to the board)", ref="J8")
    S(sc, "T2", "J", "THERMOSTAT", FP["t2"], {"1": "SW", "2": "HT_C"},
      "Thermostat (bimetal, NO, closes at 45 degC; layout 3.7): 1 = +24V_SW, 2 = to the hot-follower coil", ref="J9")
    d.R(sc, "TMR_RET", "GND", "47k", "Timer return pull-down")
    d.PTC(sc, "SW", "PANEL_SUP", "PTC 0.1A hold", FP["f1206"], "Panel switch supply fuse (two follower inputs, about 28 mA at 26.4 V)")
    S(sc, "T4", "J", "PANEL", FP["t4"], {"1": "PANEL_SUP", "2": "SPEED_OBS", "3": "STEP"},
      "Dial-strip controls (offset-dial README 5): 1 = +24V_SW switch supply (fused), 2 = speed selector return (closed = observation), "
      "3 = single-step button return (NO), 4 = spare", nc=("4",), ref="J10")
    return d


def counts(d: Design) -> dict:
    out: dict[str, int] = {}
    for p in d.parts:
        out[p.kind] = out.get(p.kind, 0) + 1
    return out


if __name__ == "__main__":
    dd = build()
    print(len(dd.parts), counts(dd), len(dd.relays))
