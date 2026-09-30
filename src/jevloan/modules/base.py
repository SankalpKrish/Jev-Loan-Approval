"""Registry and wire-format builders for the question packs (PLAN section 3.8).

A `QuestionDef` describes one question: its id, module, stage, type, the segments it applies to, how a "yes" or a
high level reads for credit risk (`risk_polarity`), and either a static `wire` dict or a `build(state)` function
that makes the wire dict from the state. Packs register their questions with `register`; the pipeline asks
`questions_for(stage, segment, state)` for the wire dicts to send.

The three builders (`noul_wire`, `score_wire`, `choice_wire`) produce the structured rubric format from the
vendor's structured-criteria guidance and validate it: 2 to 3 examples per side or level, a non-empty `what`, and
`refer_to` entries that are dotted state paths in backticks. `validate_wire` applies the same rules to any wire
dict, so `jevloan modules check` can hold every pack (including hand-built wires) to them.
"""

from __future__ import annotations

import copy
import importlib
import importlib.util
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

Polarity = Literal["yes_is_good", "yes_is_bad", "ordinal_high_is_good"]
ALL_SEGMENTS = frozenset({"salaried_personal", "self_employed", "msme_business", "secured_home"})
STAGES = ("appraisal", "sanction_docs", "monitoring")

_QTYPES = ("noul", "choice", "score")
_POLARITIES = ("yes_is_good", "yes_is_bad", "ordinal_high_is_good")
_MODULES = ("A", "B", "C", "D", "E", "F")
_PACK_MODULES = ("a_readiness", "b_fraud", "c_appraisal", "d_triage", "e_memo_kfs", "f_monitoring")

MIN_EXAMPLES = 2
MAX_EXAMPLES = 3

# `refer_to` entry: a dotted path to a state field, in backticks. Segments are names, list indices (`0`) or
# name[index]. A single name such as `documents` is a path too.
_SEGMENT = r"(?:[A-Za-z_][A-Za-z0-9_]*(?:\[\d+\])?|\d+)"
REF_PATH_RE = re.compile(rf"^`{_SEGMENT}(?:\.{_SEGMENT})*`$")


# --- the question definition and the registry --------------------------------------------------------------


@dataclass(frozen=True)
class QuestionDef:
    qid: str
    module: str
    stage: str
    qtype: Literal["noul", "choice", "score"]
    segments: frozenset[str]
    risk_polarity: Polarity
    routing_relevant: bool
    reason_text: str  # short human reason, used by the policy engine when this question drives an outcome
    wire: dict | None = None  # static wire-format question
    build: Callable[[dict], dict] | None = None  # state -> wire dict (questions parameterised by state)
    applies: Callable[[dict], bool] | None = None  # extra applicability test on the state (beyond segments)

    def __post_init__(self) -> None:
        if not isinstance(self.segments, frozenset):
            object.__setattr__(self, "segments", frozenset(self.segments))
        problems = []
        if self.module not in _MODULES:
            problems.append(f"module must be one of {_MODULES}, got {self.module!r}")
        if not self.qid.startswith(f"{self.module}_") or len(self.qid) <= 2:
            problems.append(f"qid {self.qid!r} must start with '{self.module}_'")
        if self.stage not in STAGES:
            problems.append(f"stage must be one of {STAGES}, got {self.stage!r}")
        if self.qtype not in _QTYPES:
            problems.append(f"qtype must be one of {_QTYPES}, got {self.qtype!r}")
        if self.risk_polarity not in _POLARITIES:
            problems.append(f"risk_polarity must be one of {_POLARITIES}, got {self.risk_polarity!r}")
        if not self.segments or not self.segments <= ALL_SEGMENTS:
            problems.append(f"segments must be a non-empty subset of {sorted(ALL_SEGMENTS)}, got {sorted(self.segments)}")
        if not self.reason_text.strip():
            problems.append("reason_text must not be empty")
        if (self.wire is None) == (self.build is None):
            problems.append("give exactly one of wire and build")
        if self.wire is not None and self.wire.get("type") != self.qtype:
            problems.append(f"wire type {self.wire.get('type')!r} does not match qtype {self.qtype!r}")
        if problems:
            raise ValueError(f"invalid QuestionDef {self.qid!r}: " + "; ".join(problems))

    def to_wire(self, state: dict) -> dict:
        """The wire-format question for this state (a fresh copy, safe to mutate)."""
        wire = self.build(state) if self.build is not None else copy.deepcopy(self.wire)
        if wire.get("type") != self.qtype:
            raise ValueError(f"{self.qid}: wire type {wire.get('type')!r} does not match qtype {self.qtype!r}")
        return wire

    def is_applicable(self, segment: str, state: dict) -> bool:
        if segment not in self.segments:
            return False
        return True if self.applies is None else bool(self.applies(state))


REGISTRY: dict[str, QuestionDef] = {}


def register(q: QuestionDef) -> QuestionDef:
    """Add a question to the registry. Raises ValueError on a duplicate qid. Returns `q`."""
    if q.qid in REGISTRY:
        raise ValueError(f"question {q.qid!r} is already registered")
    REGISTRY[q.qid] = q
    return q


def load_all() -> None:
    """Import every pack module so its questions register. A module is skipped only if it does not exist yet;
    an import error inside one propagates."""
    for name in _PACK_MODULES:
        full = f"{__package__}.{name}"
        if importlib.util.find_spec(full) is None:
            continue
        importlib.import_module(full)


def catalog() -> dict[str, QuestionDef]:
    load_all()
    return dict(REGISTRY)


def __getattr__(name: str) -> Any:
    """`ALL_QUESTIONS` (PLAN 3.8): the live qid -> QuestionDef registry, with every pack loaded. It is resolved on
    first access, so `base.ALL_QUESTIONS` and `from ...base import ALL_QUESTIONS` are never empty by accident."""
    if name == "ALL_QUESTIONS":
        load_all()
        return REGISTRY
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def questions_for(stage: str, segment: str, state: dict) -> dict[str, dict]:
    """qid -> wire dict for every registered question of `stage` that applies to this segment and state.

    The order is deterministic (module, then qid), whatever order the packs were imported in."""
    load_all()
    chosen = [q for q in REGISTRY.values() if q.stage == stage and q.is_applicable(segment, state)]
    chosen.sort(key=lambda q: (q.module, q.qid))
    return {q.qid: q.to_wire(state) for q in chosen}


# --- wire-format builders and validation -------------------------------------------------------------------


def _text_problems(where: str, value: Any, *, required: bool) -> list[str]:
    if value is None:
        return [f"{where} is missing"] if required else []
    if not isinstance(value, str) or not value.strip():
        return [f"{where} must be a non-empty string"]
    return []


def _side_problems(where: str, side: Any, *, needs_what: bool = True) -> list[str]:
    if not isinstance(side, dict):
        return [f"{where} must be an object with what/examples"]
    problems = _text_problems(f"{where}.what", side.get("what"), required=needs_what)
    if "not_for" in side:
        problems += _text_problems(f"{where}.not_for", side["not_for"], required=False)
    examples = side.get("examples")
    if not isinstance(examples, list) or not (MIN_EXAMPLES <= len(examples) <= MAX_EXAMPLES):
        count = len(examples) if isinstance(examples, list) else "none"
        problems.append(f"{where}.examples needs {MIN_EXAMPLES} to {MAX_EXAMPLES} entries, has {count}")
    elif not all(isinstance(e, str) and e.strip() for e in examples):
        problems.append(f"{where}.examples entries must be non-empty strings")
    return problems


def _instruction_problems(instructions: Any) -> list[str]:
    if not isinstance(instructions, dict):
        return ["instructions must be an object"]
    problems = _text_problems("instructions.question", instructions.get("question"), required=True)
    if "focus" in instructions:
        problems += _text_problems("instructions.focus", instructions["focus"], required=False)
    refer_to = instructions.get("refer_to")
    if not isinstance(refer_to, list) or not refer_to:
        problems.append("instructions.refer_to must be a non-empty list of state paths in backticks")
    else:
        for entry in refer_to:
            if not isinstance(entry, str) or not REF_PATH_RE.match(entry):
                problems.append(f"instructions.refer_to entry {entry!r} is not a dotted state path in backticks")
    return problems


def validate_wire(wire: Any) -> list[str]:
    """Every rubric-shape problem in a wire dict (an empty list means it is fine)."""
    if not isinstance(wire, dict):
        return ["wire must be an object"]
    kind = wire.get("type")
    problems = _instruction_problems(wire.get("instructions"))
    criteria = wire.get("criteria")
    if kind == "noul":
        if not isinstance(criteria, dict) or set(criteria) != {"true", "false"}:
            return problems + ["noul criteria must have exactly the keys true and false"]
        problems += _side_problems("criteria.true", criteria["true"])
        problems += _side_problems("criteria.false", criteria["false"])
    elif kind == "score":
        if not isinstance(criteria, list) or not (2 <= len(criteria) <= 10):
            return problems + ["score criteria must be a list of 2 to 10 levels"]
        names = []
        for i, level in enumerate(criteria):
            where = f"criteria[{i}]"
            problems += _side_problems(where, level)
            problems += _text_problems(f"{where}.level", level.get("level") if isinstance(level, dict) else None, required=True)
            names.append(level.get("level") if isinstance(level, dict) else None)
        if len(set(names)) != len(names):
            problems.append("score level names must be distinct")
    elif kind == "choice":
        if not isinstance(criteria, dict) or len(criteria) < 2:
            return problems + ["choice criteria must be an object with at least 2 options"]
        for option, spec in criteria.items():
            problems += _side_problems(f"criteria[{option!r}]", spec)
    else:
        problems.append(f"type must be noul, choice or score, got {kind!r}")
    return problems


def _instructions(question: str, refer_to: list[str], focus: str | None, data: dict | None) -> dict:
    instructions: dict[str, Any] = {"question": question}
    if focus is not None:
        instructions["focus"] = focus
    instructions["refer_to"] = list(refer_to)
    if data is not None:
        instructions["data"] = data
    return instructions


def _checked(wire: dict) -> dict:
    problems = validate_wire(wire)
    if problems:
        raise ValueError("invalid rubric: " + "; ".join(problems))
    return wire


def noul_wire(
    question: str,
    *,
    refer_to: list[str],
    true_what: str,
    true_examples: list[str],
    false_what: str,
    false_examples: list[str],
    false_not_for: str | None = None,
    focus: str | None = None,
    data: dict | None = None,
) -> dict:
    """A Noul question. The true side is always a plain "yes" to `question` as worded."""
    false: dict[str, Any] = {"what": false_what}
    if false_not_for is not None:
        false["not_for"] = false_not_for
    false["examples"] = list(false_examples)
    return _checked(
        {
            "type": "noul",
            "instructions": _instructions(question, refer_to, focus, data),
            "criteria": {"true": {"what": true_what, "examples": list(true_examples)}, "false": false},
        }
    )


def score_wire(
    question: str,
    *,
    refer_to: list[str],
    levels: list[dict],
    focus: str | None = None,
    data: dict | None = None,
) -> dict:
    """A Score question. `levels` is ordered worst to best: [{"level", "what", "not_for", "examples"}, ...]."""
    criteria = []
    for level in levels:
        entry = {"level": level.get("level"), "what": level.get("what")}
        if level.get("not_for") is not None:
            entry["not_for"] = level["not_for"]
        entry["examples"] = list(level.get("examples") or [])
        criteria.append(entry)
    return _checked({"type": "score", "instructions": _instructions(question, refer_to, focus, data), "criteria": criteria})


def choice_wire(
    question: str,
    *,
    refer_to: list[str],
    options: dict[str, dict],
    focus: str | None = None,
    data: dict | None = None,
) -> dict:
    """A Choice question. `options` maps each option to {"what", "not_for", "examples"}."""
    criteria = {}
    for option, spec in options.items():
        entry = {"what": spec.get("what")}
        if spec.get("not_for") is not None:
            entry["not_for"] = spec["not_for"]
        entry["examples"] = list(spec.get("examples") or [])
        criteria[option] = entry
    return _checked({"type": "choice", "instructions": _instructions(question, refer_to, focus, data), "criteria": criteria})
