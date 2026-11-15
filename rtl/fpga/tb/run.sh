#!/usr/bin/env bash
# Run the FPGA bridge/engine cocotb tests in WSL.  Usage: rtl/fpga/tb/run.sh [pytest args]
# Same conventions as rtl/tb/run.sh (SIM=verilator|icarus, venv ~/venvs/christopher-rtl,
# builds in ~/build/christopher-rtl).  Run only one simulation at a time (WSL OOM).
set -euo pipefail
export PATH="$HOME/opt/oss-cad-suite/bin:$PATH"
export SIM="${SIM:-verilator}"
export CHRISTOPHER_BUILD="${CHRISTOPHER_BUILD:-$HOME/build/christopher-rtl}"
export PYTHONDONTWRITEBYTECODE=1
# shellcheck disable=SC1090
source "${CHRISTOPHER_VENV:-$HOME/venvs/christopher-rtl}/bin/activate"
cd "$(dirname "$(readlink -f "$0")")"
mkdir -p "$CHRISTOPHER_BUILD"
exec python -m pytest -v -s --durations=0 -p no:cacheprovider "$@" test_fpga.py
