"""The default exhibit set: name -> (spec, seed)."""

from __future__ import annotations

from .engine import Spec

# 4 slice scenarios (6 units: 4 letters, 6 edges, 3 loops, right/slow drum fixed) and
# 2 full-run scenarios (12 units: 10 letters, 12 edges, 3 loops, 17,576 positions).
DEFAULT_SET: list[tuple[Spec, int]] = [
    (Spec("slice-6-a", "slice", 6, 4, max_span=12, false_min=0, false_max=0), 1),
    (Spec("slice-6-b", "slice", 6, 4, max_span=12, false_min=0, false_max=0), 2),
    (Spec("slice-6-c", "slice", 6, 4, max_span=12, false_min=1, false_max=1), 3),
    (Spec("slice-6-d", "slice", 6, 4, max_span=12, false_min=1, false_max=2), 4),
    (Spec("full-12-a", "full", 12, 10, max_span=16, false_min=0, false_max=0), 11),
    (Spec("full-12-b", "full", 12, 10, max_span=16, false_min=1, false_max=1), 12),
]
