# Project Christopher

앨런 튜링의 에니그마 해독기 **봄베(Bombe)** 를 재현하는 프로젝트.

소프트웨어 모델(Rust 코어 + Python 조작 인터페이스)에서 시작해 RTL, 실물까지 단계적으로 진행하고, LaTeX 보고서로 마무리한다.
재현 범위는 튜링-웰치먼 3로터 봄베와 해군 4로터(M4)까지다.

## 현재 상태

- **Phase 0 (기반): 완료.** 자료 조사, 배선표, 검증 벡터, 빌드 구성.
- **Phase 1 (소프트웨어): 동작.** 에니그마 I / M3 / M4, 메뉴 생성기, 3·4로터 봄베, 체킹 머신, 전체 탐색, Python 조작 인터페이스. 크립에서 평문까지 끝까지 해독한다.
- Phase 2 (RTL) 이후는 미착수.

전체 계획, 로드맵, 디렉토리 구조는 [docs/PLAN.md](docs/PLAN.md)에 있다.

## Quickstart

필요한 것: Rust 툴체인, Python 3.10 이상, [maturin](https://www.maturin.rs/).

### 빌드 출력 위치

이 저장소는 Dropbox 폴더 안에 있다. Dropbox가 `target/` 안의 파일을 잠가 빌드가 실패하는 문제가 실제로 발생했기 때문에, **빌드 출력과 Python 가상환경은 Dropbox 밖에 둔다.**

| 대상 | 위치 | 설정 |
|---|---|---|
| Cargo 빌드 출력 | `%LOCALAPPDATA%/christopher/target` | `.cargo/config.toml` (PC별 설정, git 제외) |
| Python 가상환경 | `%LOCALAPPDATA%/christopher/venv` | 수동 생성 |

프로젝트 폴더 안에는 가상환경을 만들지 않는다. 다른 PC에서는 `.cargo/config.toml`의 `target-dir`과 아래 경로를 그 PC에 맞게 바꾼다.

### 빌드

Python 바인딩을 가상환경에 설치한다 (Git Bash 기준, 저장소 루트에서 실행).

```bash
export VIRTUAL_ENV="$LOCALAPPDATA/christopher/venv"
$VIRTUAL_ENV/Scripts/maturin.exe develop --release
```

PowerShell에서는 `$env:VIRTUAL_ENV = "$env:LOCALAPPDATA/christopher/venv"` 뒤에 `& "$env:VIRTUAL_ENV/Scripts/maturin.exe" develop --release`.

### 시험

```bash
cargo test -p christopher-core
$VIRTUAL_ENV/Scripts/python.exe -m pytest tests
```

### 사용 예

암호문과 크립(추정 평문)에서 키와 평문을 복원한다.

```python
from christopher import Enigma, search

plain = "ANXOBERKOMMANDODERWEHRMACHTXWETTERVORHERSAGEFUERDIENORDSEEXHEUTEKLAR"
secret = Enigma("enigma-i", rotors=("III", "V", "II"), reflector="B", rings="GMC",
                positions="KDC", plugboard="AQ BZ CR DM EV FW GK HN IU JP")
cipher = secret.encrypt(plain)

# 크립 WETTERVORHERSAGEFUERDIE가 암호문의 28번째 글자(0부터)에서 시작한다고 보고 전체 로터 순서를 탐색
report = search(cipher, "WETTERVORHERSAGEFUERDIE", 28, model="enigma-i", max_edges=14)
print(report)                 # 120 wheel orders, 2109120 positions, ...
print(report.solutions[0])    # 로터 순서, 링, 시작 위치, 플러그보드, 평문
```

메뉴 작성 → 배선 → 구동 → 정지 확인 → 체킹을 단계별로 따라 하려면 노트북 [notebooks/01_crib_to_plaintext.ipynb](notebooks/01_crib_to_plaintext.ipynb)를 본다. 노트북 실행에는 `ipykernel`과 `matplotlib`이 필요하다(같은 가상환경에 설치).

M4 전체 탐색(로터 순서 1,344개, 약 6.1억 위치)의 예:

```bash
cargo run --release -p christopher-core --example full_search m4
```

## 문서

- [docs/PLAN.md](docs/PLAN.md) — 목표, 아키텍처, 로드맵, 디렉토리 구조
- [docs/reference/bombe-mechanics.md](docs/reference/bombe-mechanics.md) — 3로터 봄베의 기구와 동작 (출처 포함)
- [docs/reference/four-rotor-and-relay-bombes.md](docs/reference/four-rotor-and-relay-bombes.md) — 4로터 봄베와 릴레이식 봄베
- [report/references.bib](report/references.bib) — 참고문헌
