# Drum PCBs (gear bombe, track G)

Generated KiCad boards for the bombe drums of Enigma rotors I–V (VI–VIII are supported by the
generator: `--all`). One board = one drum = one rotor wiring, realised twice (two ring pairs).
Source of truth is `data/wirings/enigma.txt`; the boards are build products of `gen_drum.py`.
Design basis: `hardware/gear/concept.md` sections 2–4, `hardware/common/design-brief.md` 4.2.

## Files

| File | Purpose |
|---|---|
| `gen_drum.py` | wiring table -> `boards/drum_<R>.kicad_pcb` + `.kicad_pro` (stdlib only, deterministic) |
| `verify_drum.py` | independent connectivity check + `kicad-cli` DRC / IPC-D-356 / Gerber+drill / render |
| `boards/` | generated boards I–V (~130 kB each) |

## Conventions

**Rings** (index 1 = innermost; KiCad F.Cu, board centre at sheet (150, 100) mm):

| Ring | r (mm) | Pad (tangential x radial) | Side of the rotor | Wiring copy |
|---:|---:|---|---|---|
| 1 | 22 | 2.66 x 3.6 | entry (keyboard/IN side) | B |
| 2 | 29 | 3.50 x 3.6 | reflector side | B |
| 3 | 36 | 4.35 x 3.6 | reflector side | A |
| 4 | 43 | 5.20 x 3.6 | entry (keyboard/IN side) | A |

Pad tangential width = 50 % of the ring pitch (concept.md section 2). Pads are rounded rectangles
(0.3 mm corner), long axis radial; the contact tip runs along the ring circle through the pad centre.

**Pad numbering.** Pad name = `<ring><letter>`, e.g. `4C`; letter index i = 0..25 (A..Z) is the
Enigma contact index of the rotor wiring.

**Wiring (the one mapping rule).** With W = the table row of `enigma.txt` (input letter i on the
entry side -> output W[i] on the reflector side, core offset 0):

    copy A:  pad 4<i>  <->  pad 3<W[i]>        (outer <-> 3rd ring)
    copy B:  pad 1<i>  <->  pad 2<W[i]>        (inner <-> 2nd ring)

Nets are named after the connection (`4A-3E`, `1A-2E`, ...). Both copies use the same orientation
(entry = ring 4 / ring 1), so the return path through ring 2 -> ring 1 realises W^-1 automatically.

**Scrambler stack** (concept.md 3.1, current flows entry -> reflector via copy A, back via copy B):
IN - lower drum ring4 | lower ring3 - middle ring4 | middle ring3 - upper ring4 | upper ring3 -
reflector - upper ring2 | upper ring1 - middle ring2 | middle ring1 - lower ring2 | lower ring1 - OUT.
Because every drum is oriented entry -> reflector, **the same board serves in any slot**: upper/fast
drum = Enigma left rotor, middle = middle, lower/slow = right rotor.

**Angles and sides.** The pad side (F.Cu, the "front") faces the spring contacts. Seen from the pad
side, letters run clockwise starting at the index mark (letter A at 12 o'clock, KiCad top view):
pad i is at angle i * 360/26 = 13.846 deg * i. The back (B.Cu side, silkscreen only) is not contact
side. Mirror image on request: `gen_drum.py --letters-ccw`.

**Pad position -> drum letter -> Enigma core offset.** The contacts on the scrambler board are fixed
at 26 positions j = 0..25 (j = 0 at the reference mark, same angular grid as the pads). When the drum
shows letter L at the reference mark, contact j touches pad index (j + o) mod 26 with o = L (A = 0).
This is exactly the core's rotor map `c -> W[c + o] - o` (`crates/christopher-core`, `enigma.rs`,
`bombe.rs`): entry contact j meets pad j+o, leaves on reflector-side pad W[j+o], which sits under
contact W[j+o]-o. The drum therefore steps by one letter (o -> o+1) when it turns **counter-clockwise
as seen from the pad side** (clockwise as seen from the back). The bombe letter-ring offsets (I-III,
VI-VIII 1; IV 2; V 3 steps, concept.md section 4) are a printing matter of the letter ring on the
drum body, not of the PCB.

**Mechanical features** (provisional): rim notch (R1.2) halfway between Z and A; Ø2.0 dowel hole at
r = 7.5 in the direction of letter A; four Ø3.2 holes at r = 7.5, 45 + 90k deg; Ø8.0 centre bore.
Silkscreen front: letters A–Z at r = 46.3 (feet towards the centre), index triangle at A, rotor name.
Back: rotor name, wiring string, turnover letter(s) (information only), convention note.

## Layer stack and routing (4 layers; 6 not needed)

| Layer | Content |
|---|---|
| F.Cu | 104 pads; short stubs from pad to via, in the pad-free annulus next to each ring |
| In1.Cu | radial tracks inside the pad-free "gap columns" between two neighbouring pads |
| In2.Cu | one arc per connection on a private lane (A: r 28.4–37.4, B: r 11.4–22.8, 0.62 mm pitch, lanes shared by non-overlapping arcs; 9–11 lanes used of 15 / 19) |
| B.Cu | silkscreen only (no copper) |

Per connection: pad - stub - via - radial (In1) - via - arc (In2) - via - radial (In1) - via - stub -
pad (4 vias, 2 arcs on F.Cu, 1 on In2, 2 radials). The whole 208 vias per board lie in pad-free zones, so the contact path (ring
circle) never meets a via or a track except short mask-covered stubs. Copy A and copy B are routed in
separate radius ranges and share layers. Stackup written in the board: 1.6 mm, 35 / 17.5 / 17.5 / 35 um Cu,
prepreg 0.2104, core 1.065 (typical 4-layer 1.6 mm), finish "Hard Gold" on front pads.

## Fab rules targeted (stored in `boards/*.kicad_pro`)

Track / space 0.15 / 0.15 mm (standard 6/6 mil, looser than the 0.127 low-cost limit); via drill 0.3 /
pad 0.6 mm (annular ring 0.15), tented both sides; copper-to-edge >= 0.3; hole-to-hole >= 0.25; NPTH
holes >= 2.0 mm; silkscreen text >= 1.0 mm high, stroke 0.15–0.25. Surface finish: hard (electrolytic) gold on the F.Cu pad
openings, ask the fab for >= 0.8 um Au over >= 3–5 um Ni; ENIG is the fallback for prototypes.

## Regenerate and verify

    python hardware/gear/drum-pcb/gen_drum.py            # I..V   (--all for I..VIII, --out DIR)
    python hardware/gear/drum-pcb/verify_drum.py --drc --ipc --fab --render I

`verify_drum.py` needs `kicad-cli` (set `KICAD_CLI`, or it searches PATH and `Program Files`). Build output
(DRC reports, Gerbers, drill, D-356, PNG renders) goes to `%LOCALAPPDATA%/christopher/drum-pcb`
(`$XDG_CACHE_HOME/christopher/drum-pcb` elsewhere), or `--build-dir`; nothing is written into the repo.
Checks: (1) geometric connectivity parsed from the board file alone: 52 islands, each exactly the two pads
required by `enigma.txt`, net labels consistent, pad positions on the documented grid; (2) KiCad DRC
must report 0 violations and 0 unconnected items (shorts are errors); (3) IPC-D-356 net list equals
the wiring table; (4) Gerbers (F/In1/In2/B Cu, masks, silk, edge) + Excellon PTH/NPTH export.
Last result (KiCad 10.0.4): rotors I–VIII all pass all four.

## Open questions for the mechanical design

1. Axle: bore Ø8, key, and the 26-position face coupling are placeholders; the dowel/M3 pattern only
   fixes one orientation of PCB-to-body (the letter ring sits on the body).
2. Rotation direction and whether the contact boards see the pad side as assumed (letters cw, step ccw
   seen from the pad side); `--letters-ccw` mirrors the board if the machine turns the other way.
3. Hard gold on pad openings only: confirm availability/price (plating leads on inner layers), else ENIG.
4. Solder mask currently covers the gaps between pads (mask edge 0.05 outside each pad). concept.md 3.3
   suggests bare/flattened gaps; decide after the G.1 wear test (mask is ~10–20 um below the gold).
5. Pad radial length 3.6 mm (+/-1.8) and tangential tolerance +/-2.1 deg (concept 2): confirm with the
   real contact tip and axial play.
6. Optional identification pads on the back (concept 6) are not generated yet.
7. Rim margin (3.2 mm outside ring 4) and body diameter/skirt; PCB flatness (back side has no copper,
   consider a stiffener or 2.0 mm board).

## 기구 설계 반영 (2026-11-15, `hardware/gear/layout.md`)

- 중심 구멍 Ø8.0 → **Ø8.6** (축 파일럿 Ø8 h6에 대한 여유 구멍; 중심 맞춤은 드럼 몸체 부싱이 한다).
- 패드 면 중심 r ≤ 17.5는 26이빨 인덱스 원판(Ø34)의 자리다. 로터 이름 실크는 패드 면에서 지우고(뒷면 표기만 남김), 기준 삼각형은 r 17.8–19.9로 옮겼다.
- **미결: 이 영역의 비아.** 배선 B벌의 일부 레인이 r 11.4–22.8에 있어 덮개 비아가 영역 안에 남는다 — 로터 I 0개, II 2개, III 8개, IV 8개, V 0개. 해결안: (a) B벌 레인을 r > 17.5로 옮기는 배선 재설계(레인 수 부족 가능), (b) 인덱스 원판 안쪽에 얕은 홈(비아 덮개 높이 수십 µm 수준이라 접촉면만 피하면 됨). G.1에서 결정.
- 평탄도: r = 7.5 고정만으로는 가장자리 휨(통상 0.72 mm / 96 mm)이 허용치(r = 43에서 ±0.3 mm)를 넘을 수 있다. 뒷면 구리 채움, r = 46.3의 Ø2.2 구멍 4개(글자 사이, 노치와 겹치지 않게), 또는 몸체 접착 중 G.1에서 결정.
