"""Entry point on the Pico: copy tester_core.py, hal_pico.py and this file to the board.

Reads command lines from USB serial (stdin), writes replies to stdout. Safe state at boot:
all drivers off.
"""

import sys

from hal_pico import PicoHal
from tester_core import Tester


def main():
    tester = Tester(PicoHal())
    while True:
        line = sys.stdin.readline()
        if not line:
            continue
        for reply in tester.handle(line):
            sys.stdout.write(reply + "\n")


main()
