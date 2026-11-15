"""Printable A5 menu card (LaTeX/TikZ, compile with xelatex) from a scenario record."""

from __future__ import annotations

import math
import random
import shutil
import subprocess
from pathlib import Path


def _crossings(order: list[str], edges: list[tuple[str, str]]) -> int:
    pos = {c: i for i, c in enumerate(order)}
    n = 0
    for i in range(len(edges)):
        a, b = sorted((pos[edges[i][0]], pos[edges[i][1]]))
        for j in range(i + 1, len(edges)):
            c, d = sorted((pos[edges[j][0]], pos[edges[j][1]]))
            if len({a, b, c, d}) < 4:
                continue
            if (a < c < b) != (a < d < b):
                n += 1
    return n


def circle_order(letters: str, edges: list[tuple[str, str]]) -> list[str]:
    """Order the letters on a circle with few edge crossings (random restarts + swap hill climbing)."""
    rng = random.Random(7)
    best, best_x = list(letters), None
    for _ in range(60):
        cur = list(letters)
        rng.shuffle(cur)
        x = _crossings(cur, edges)
        improved = True
        while improved and x:
            improved = False
            for i in range(len(cur)):
                for j in range(i + 1, len(cur)):
                    cur[i], cur[j] = cur[j], cur[i]
                    y = _crossings(cur, edges)
                    if y < x:
                        x, improved = y, True
                    else:
                        cur[i], cur[j] = cur[j], cur[i]
        if best_x is None or x < best_x:
            best, best_x = list(cur), x
        if best_x == 0:
            break
    return best


def graph_tikz(rec: dict, radius: float = 2.6) -> str:
    m = rec["menu"]
    edges = [(e["plain"], e["cipher"]) for e in m["edges"]]
    order = circle_order(m["letters"], edges)
    n = len(order)
    xy = {}
    for i, ch in enumerate(order):
        ang = math.pi / 2 + 2 * math.pi * i / n + (math.pi / n if n % 2 == 0 and n == 4 else 0)
        xy[ch] = (radius * math.cos(ang), radius * math.sin(ang))
    out = []
    labels: list[tuple[float, float]] = []
    for e in m["edges"]:
        (x1, y1), (x2, y2) = xy[e["plain"]], xy[e["cipher"]]
        out.append(f"\\draw[edge] ({x1:.3f},{y1:.3f}) -- ({x2:.3f},{y2:.3f});")
        for t in (0.5, 0.3, 0.7, 0.2, 0.8, 0.4, 0.6, 0.15, 0.85):
            lx, ly = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            if all((lx - a) ** 2 + (ly - b) ** 2 > 0.45 ** 2 for a, b in labels):
                break
        labels.append((lx, ly))
        out.append(f"\\node[lnk] at ({lx:.3f},{ly:.3f}) {{{e['link']}}};")
    for ch in order:
        x, y = xy[ch]
        style = "vtx,testv" if ch == m["test_letter"] else "vtx"
        out.append(f"\\node[{style}] at ({x:.3f},{y:.3f}) {{{ch}}};")
    return "\n".join(out)


PREAMBLE = r"""\documentclass[10pt]{article}
\usepackage[a5paper,margin=9mm,top=8mm,bottom=8mm]{geometry}
\usepackage{kotex}
\usepackage{xcolor}
\usepackage{tikz}
\usepackage{array,booktabs,graphicx}
\renewcommand{\familydefault}{\sfdefault}
\setsanshangulfont{UnDotum}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\definecolor{ink}{HTML}{1b2a41}
\definecolor{acc}{HTML}{b3541e}
\definecolor{soft}{HTML}{8a8f98}
\definecolor{paper}{HTML}{f4efe3}
\color{ink}
\tikzset{
  vtx/.style={circle,draw=ink,fill=paper,line width=0.9pt,minimum size=9mm,inner sep=0pt,font=\Large\bfseries\ttfamily},
  testv/.style={draw=acc,line width=2pt},
  edge/.style={line width=1pt,color=ink},
  lnk/.style={circle,draw=acc,fill=white,text=acc,line width=0.7pt,minimum size=5.2mm,inner sep=0pt,font=\small\bfseries},
}
\newcommand{\mono}[1]{{\ttfamily\bfseries #1}}
\newcommand{\sect}[1]{\par\medskip{\color{acc}\bfseries\small #1}\par\vspace{1pt}\hrule height 0.4pt\vspace{3pt}}
\begin{document}
"""


def _strip(rec: dict) -> str:
    """Alignment strip: link numbers, crib letters, cipher letters under them (used links highlighted)."""
    crib, ct = rec["crib"], rec["crib_cipher"]
    used = {e["link"] for e in rec["menu"]["edges"]}
    cols = "c" * len(crib)

    def cell(i, ch):
        if i + 1 in used:
            return rf"\textcolor{{acc}}{{\mono{{{ch}}}}}"
        return rf"\textcolor{{soft}}{{\ttfamily {ch}}}"

    nums = " & ".join(
        (rf"\textcolor{{acc}}{{\small\bfseries {i + 1}}}" if i + 1 in used else rf"\textcolor{{soft}}{{\small {i + 1}}}")
        for i in range(len(crib))
    )
    row1 = " & ".join(cell(i, ch) for i, ch in enumerate(crib))
    row2 = " & ".join(cell(i, ch) for i, ch in enumerate(ct))
    return (
        rf"\setlength{{\tabcolsep}}{{3.1pt}}\begin{{tabular}}{{l{cols}}}"
        rf"링크 & {nums}\\ 크립 & {row1}\\ 암호 & {row2}\end{{tabular}}"
    )


def groups5(text: str) -> str:
    return " ".join(text[i:i + 5] for i in range(0, len(text), 5))


def _wrap(groups: str, per_line: int = 4) -> str:
    g = groups.split()
    return r"\\ ".join(" ".join(g[i:i + per_line]) for i in range(0, len(g), per_line))


def front(rec: dict) -> str:
    m = rec["menu"]
    mode = rec["mode"]
    wo = rec["wheel_order"]
    ts = rec["true_stop"]
    lo, hi = rec["crib_letters_1based"]
    if mode == "slice":
        rng = (rf"오른쪽(느린) 드럼을 \mono{{{ts['fixed_right_drum']}}} 에 고정하고 왼쪽·가운데 드럼만 "
               rf"한 바퀴씩 돈다 (26$\times$26 = 676 위치).")
    else:
        rng = r"세 드럼 전체를 돈다 (17,576 위치)."
    short = r"26$\times$26 구간" if mode == "slice" else "전체 구동"
    return rf"""
{{\large\bfseries 메뉴 카드}} \hfill {{\small\color{{soft}} Menükarte \mono{{{rec['id']}}}}}\par
{{\small 스크램블러 \mono{{{m['n_edges']}}}개 \textperiodcentered{{}} 글자 \mono{{{m['n_letters']}}}개 \textperiodcentered{{}} 루프 \mono{{{m['loops']}}}개
\textperiodcentered{{}} {short}}}\par
\sect{{1. 전문 (Funkspruch)}}
\begin{{center}}\ttfamily\bfseries\large {_wrap(rec['ciphertext_groups'])}\end{{center}}
\sect{{2. 크립 (추정 평문)}}
{{\small 평문에 \mono{{{rec['crib']}}} 가 있다고 추정한다. 전문의 {lo}번째부터 {hi}번째 글자 아래에 놓는다.
색깔 링크 번호가 이 카드의 스크램블러다.}}\par\smallskip
\begin{{center}}{_strip(rec)}\end{{center}}
\sect{{3. 메뉴 (글자 그래프)}}
\begin{{center}}\begin{{tikzpicture}}[scale=0.92]
{graph_tikz(rec)}
\end{{tikzpicture}}\end{{center}}
\vspace{{-2pt}}
{{\small 둥근 번호 = 링크 번호 = 크립 위치. 굵은 주황 테두리 글자 \mono{{{m['test_letter']}}} = 테스트 글자.}}\par
\sect{{4. 기계 설정}}
{{\small 반사판 \mono{{{wo['reflector']}}} \quad 로터(왼쪽$\to$오른쪽, 위에서 아래 드럼) \mono{{{' - '.join(wo['rotors_left_to_right'])}}}\\
{rng}}}
\newpage
"""


def back(rec: dict) -> str:
    m = rec["menu"]
    ts = rec["true_stop"]
    rows = []
    for e in m["edges"]:
        rows.append(
            rf"{e['unit']} & {e['link']} & \mono{{{e['plain']}}} & \mono{{{e['cipher']}}} & "
            rf"\mono{{{e['drum_offset_letters']}}} & $+{e['crib_index']}$ \\"
        )
    stop_rows = []
    for s in rec["run"]["expected_stops"]:
        mark = r"\textbf{정답 위치}" if s["is_true"] else "거짓 정지"
        cand = ",".join(s["stecker_candidates"]) if len(s["stecker_candidates"]) <= 3 else f"{len(s['stecker_candidates'])}개"
        stop_rows.append(rf"\mono{{{s['drums']}}} & \mono{{{cand}}} & {mark} \\")
    blank = "\n".join(r"\rule{0pt}{5.5mm} & & \\ \midrule" for _ in range(3))
    key = rec["key"]
    return rf"""
{{\large\bfseries 배선 안내}} \hfill {{\small\color{{soft}} \mono{{{rec['id']}}}}}\par
\sect{{스크램블러 배선 (유닛마다 IN/OUT 글자 잭 한 쌍)}}
\begin{{center}}\small\renewcommand{{\arraystretch}}{{1.15}}
\begin{{tabular}}{{ccccc c}}\toprule
유닛 & 링크 & IN 글자 & OUT 글자 & 드럼 오프셋 & 오른쪽 드럼 \\ \midrule
{chr(10).join(rows)}
\bottomrule\end{{tabular}}\end{{center}}
{{\small 드럼 오프셋은 정지값 기준 상대값이다 (왼쪽 \mono{{A}}, 가운데 \mono{{A}}, 오른쪽 링크번호$-1$ 칸 앞).
즉 첫 링크는 \mono{{AAA}}, 둘째는 \mono{{AAB}} 처럼 오른쪽 드럼만 한 칸씩 앞선다.}}\par
\sect{{테스트 레지스터}}
{{\small 테스트 글자 \mono{{{m['test_letter']}}} 의 26선 케이블을 테스트 레지스터에 꽂고, 시험 선 플러그를 입력 선 \mono{{{m['input_wire']}}} 에 꽂는다.}}\par
\sect{{정지 기록}}
\begin{{center}}\small\begin{{tabular}}{{|p{{2.6cm}}|p{{3.4cm}}|p{{3.8cm}}|}}\hline
지시 드럼 & 꺼진 램프(스테커 후보) & 체킹 결과 \\ \hline
{blank.replace(r"\midrule", r"\hline")}
\end{{tabular}}\end{{center}}
\vfill
\begin{{center}}\color{{soft}}\small --- 아래는 정답입니다. 먼저 직접 풀어 보세요 (카드를 돌려서 읽는 방향) ---\end{{center}}
\rotatebox{{180}}{{\parbox{{\linewidth}}{{\small
{{\bfseries 정답}}\par\smallskip
이 구간의 정지 {len(rec['run']['expected_stops'])}개 (거짓 정지 {rec['run']['false_stops']}개):\par
\begin{{tabular}}{{lll}}\toprule 드럼 & 스테커 후보 & \\ \midrule
{chr(10).join(stop_rows)}
\bottomrule\end{{tabular}}\par\smallskip
정답 위치 \mono{{{ts['drums_core_letters']}}} (코어 오프셋 {ts['core_offsets'][0]},{ts['core_offsets'][1]},{ts['core_offsets'][2]};
에니그마 창 글자 \mono{{{ts['enigma_window_at_crib_start']}}}).\par
키: 로터 {' '.join(key['rotors'])}, 반사판 {key['reflector']}, 링 \mono{{{key['rings']}}}, 시작 \mono{{{key['start']}}}, \par
\mono{{{key['plugboard']}}}\par\smallskip
해독문: \mono{{{groups5(rec['plaintext'])}}}
}}}}
\end{{document}}
"""


def tex_source(rec: dict) -> str:
    return PREAMBLE + front(rec) + back(rec)


def build_pdf(rec: dict, out_dir: Path) -> Path:
    """Write ``<id>.tex`` and compile it with latexmk/xelatex into ``out_dir`` (kept outside the repo)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tex = out_dir / f"{rec['id']}.tex"
    tex.write_text(tex_source(rec), encoding="utf-8")
    cmd = shutil.which("latexmk")
    if cmd:
        args = [cmd, "-xelatex", "-interaction=nonstopmode", "-halt-on-error", f"-outdir={out_dir}", str(tex)]
    else:
        args = [shutil.which("xelatex") or "xelatex", "-interaction=nonstopmode", "-halt-on-error",
                f"-output-directory={out_dir}", str(tex)]
    r = subprocess.run(args, cwd=out_dir, capture_output=True, text=True, encoding="utf-8", errors="replace")
    pdf = out_dir / f"{rec['id']}.pdf"
    if r.returncode != 0 or not pdf.exists():
        raise RuntimeError(f"LaTeX failed for {rec['id']}:\n{r.stdout[-2000:]}\n{r.stderr[-500:]}")
    return pdf
