"""Token estimate for a state (PLAN 3.7). Calibrated against real Jev on 2026-09-29: jev-1.13.0 bills about
2.2 to 2.3 characters per token. The state is billed once per request; each question adds only its own text and
there is roughly 350 tokens of fixed overhead, so a 3k-token state is about 6.5 KB of canonical JSON."""

from __future__ import annotations

import math
from typing import Any

from jevloan.canonical import canonical_json

CHARS_PER_TOKEN = 2.2


def estimate_tokens(obj: Any) -> int:
    """``ceil(len(canonical_json(obj)) / 2.2)``."""
    return math.ceil(len(canonical_json(obj)) / CHARS_PER_TOKEN)
