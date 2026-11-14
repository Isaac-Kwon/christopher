#!/usr/bin/env bash
# Run all RTL tests in WSL.  Usage: rtl/tb/run.sh [pytest args]
# Env: SIM=verilator|icarus (default verilator), M4_FULL=1 (full M4 sweep), WAVES=1.
set -euo pipefail
export PATH="$HOME/opt/oss-cad-suite/bin:$PATH"
export SIM="${SIM:-verilator}"
export CHRISTOPHER_BUILD="${CHRISTOPHER_BUILD:-$HOME/build/christopher-rtl}"
export PYTHONDONTWRITEBYTECODE=1
# shellcheck disable=SC1090
source "${CHRISTOPHER_VENV:-$HOME/venvs/christopher-rtl}/bin/activate"
cd "$(dirname "$(readlink -f "$0")")"
mkdir -p "$CHRISTOPHER_BUILD"
exec python -m pytest -v -s --durations=0 -p no:cacheprovider "$@" .
