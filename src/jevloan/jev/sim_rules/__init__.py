"""Rule registry and helpers for the Jev simulator (PLAN section 3.4).

A rule is a function `(state, question, rng) -> answer` that stands in for Jev on one question id. It may
read the state only (the same thing real Jev sees), and returns an answer built with `noul`, `choice`
or `score`. Register it with `@sim_rule("A_income_proof_current")`, or with a glob such as
`@sim_rule("E_disclosure_*")`; an exact id beats a glob, and a longer literal part beats a shorter one.
Every module in this package is imported by `load_all()`, so a new rule needs no wiring.

    @sim_rule("A_income_proof_current")
    def _(state: dict, question: dict, rng: random.Random) -> dict:
        current = get(state, "income.months_history", 0) >= 3
        return noul(noisy_p(current, rng, accuracy=0.92))

Answers use the API's own shape (a Noul carries no confidence; the gateway derives it).
"""

import importlib
import math
import pkgutil
import random
from collections.abc import Callable, Mapping
from fnmatch import fnmatchcase
from typing import Any

SimRule = Callable[[dict, dict, random.Random], dict]

REGISTRY: dict[str, SimRule] = {}


def _is_glob(key: str) -> bool:
    return any(char in key for char in "*?[")


def sim_rule(qid_or_glob: str) -> Callable[[SimRule], SimRule]:
    """Register the decorated rule for a question id or a glob pattern."""

    def register(rule: SimRule) -> SimRule:
        existing = REGISTRY.get(qid_or_glob)
        if existing is not None and (existing.__module__, existing.__qualname__) != (rule.__module__, rule.__qualname__):
            raise ValueError(f"sim rule {qid_or_glob!r} is already registered by {existing.__module__}")
        REGISTRY[qid_or_glob] = rule
        return rule

    return register


def resolve(rules: Mapping[str, SimRule], qid: str) -> SimRule | None:
    """The rule for `qid` in `rules`: the exact id, else the matching glob with the longest literal part."""
    if qid in rules:
        return rules[qid]
    globs = [key for key in rules if _is_glob(key) and fnmatchcase(qid, key)]
    if not globs:
        return None
    return rules[max(globs, key=lambda key: sum(char not in "*?[]" for char in key))]


def lookup(qid: str) -> SimRule | None:
    return resolve(REGISTRY, qid)


def load_all() -> None:
    """Import every rule module in this package, so its `@sim_rule` decorators run. Import errors propagate."""
    for module in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{module.name}")


# --- answer builders ---------------------------------------------------------------------------------------


def _confidence(probs: list[float]) -> float:
    """Jev's confidence for a Choice or Score: 0 for a flat distribution, 1 for a certain one."""
    n = len(probs)
    return 1.0 if n == 1 else round(max(0.0, (n * max(probs) - 1) / (n - 1)), 4)


def _normalise(values: list[float]) -> list[float]:
    if not values or any(v < 0 or not math.isfinite(v) for v in values) or sum(values) <= 0:
        raise ValueError(f"probabilities must be finite, non-negative and not all zero, got {values}")
    total = sum(values)
    return [round(v / total, 4) for v in values]


def noul(p: float) -> dict:
    """A Noul answer: the probability that the answer is yes."""
    if not math.isfinite(p):
        raise ValueError(f"p must be finite, got {p}")
    return {"type": "noul", "noul": round(min(1.0, max(0.0, p)), 4)}


def choice(probs: dict[str, float]) -> dict:
    """A Choice answer from option -> weight (normalised); the first option wins a tie."""
    normalised = dict(zip(probs, _normalise(list(probs.values())), strict=True))
    top = max(normalised, key=lambda option: normalised[option])
    return {"type": "choice", "choice": top, "probabilities": normalised, "confidence": _confidence(list(normalised.values()))}


def score(probs: list[float], legend: list[str] | None = None) -> dict:
    """A Score answer from per-level weights (normalised, level 0 first). `score` is the expected level."""
    if len(probs) < 2:
        raise ValueError("a Score needs at least 2 levels")
    normalised = _normalise(list(probs))
    return {
        "type": "score",
        "score": round(sum(level * p for level, p in enumerate(normalised)), 4),
        "probabilities": {str(level): p for level, p in enumerate(normalised)},
        "legend": {str(level): legend[level] if legend else str(level) for level in range(len(normalised))},
        "confidence": _confidence(normalised),
    }


# --- helpers for writing rules -----------------------------------------------------------------------------


def get(state: Any, path: str, default: Any = None) -> Any:
    """Read `"a.b.0.c"` from nested dicts and lists; `default` if any step is missing."""
    node = state
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return default
    return node


def noisy_p(signal: bool, rng: random.Random, accuracy: float = 0.9, sharpness: float = 8.0) -> float:
    """P(yes) from a model that reads `signal` correctly `accuracy` of the time.

    Each call draws a confidence c in [0.5, 1] (mean `accuracy`; higher `sharpness` clusters it tighter),
    then reads the signal correctly with probability c and misreads it otherwise. The answer is c on the
    side it read and 1-c on the other. So a stated 0.9 is right about 90% of the time: calibrated, and
    overall right about `accuracy` of the time, on a balanced base rate. Errors come with real
    (sometimes high) confidence, as they do from a real model.
    """
    if not 0.5 <= accuracy <= 1.0:
        raise ValueError(f"accuracy must be in [0.5, 1], got {accuracy}")
    if accuracy >= 1.0:
        confidence = 1.0
    else:
        mean = min(max(2 * accuracy - 1, 0.01), 0.99)
        confidence = 0.5 + 0.5 * rng.betavariate(mean * sharpness, (1 - mean) * sharpness)
    says_yes = signal if rng.random() < confidence else not signal
    return round(confidence if says_yes else 1 - confidence, 4)


def _bump(centre: float, n_levels: int, sigma: float) -> list[float]:
    centre = min(max(centre, 0.0), n_levels - 1.0)
    weights = [math.exp(-0.5 * ((level - centre) / sigma) ** 2) for level in range(n_levels)]
    total = sum(weights)
    return [w / total for w in weights]


def level_probs(true_level: float, n_levels: int, rng: random.Random, spread: float = 0.5) -> list[float]:
    """Level weights peaked near `true_level` (feed them to `score`).

    At the default `spread` the peak lands on the true level about 90% of the time (on a neighbour
    otherwise) and the score's confidence averages about 0.88, so it is roughly calibrated. A larger
    spread means more misses and a flatter distribution.
    """
    return _bump(true_level + rng.gauss(0.0, 0.6 * spread), n_levels, 0.15 + 0.3 * spread)


def default_answer(question: dict) -> dict:
    """What the simulator answers when no rule matches: no information (0.5, uniform, or mid-scale)."""
    match question["type"]:
        case "noul":
            return noul(0.5)
        case "choice":
            return choice({option: 1.0 for option in question["criteria"]})
        case _:
            levels = len(question["criteria"])
            return score(_bump((levels - 1) / 2, levels, 0.45))
