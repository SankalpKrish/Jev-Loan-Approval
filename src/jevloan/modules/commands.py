"""`jevloan modules list | rubrics-export | check`: inspect the question packs.

* `list` prints every registered question: qid, module, stage, type, segments.
* `rubrics-export [--out docs/RUBRICS.md]` writes each question's full rubric as Markdown, grouped by module.
* `check` holds every wire dict to the rubric-shape rules and the PII gate, and exits 1 if anything fails.

Questions that are parameterised by state (a `build` function) are exercised on states built from a small
synthetic book, when the state builder (`jevloan.state`) exists; otherwise they are reported as not checked.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
from collections.abc import Iterator

from jevloan.config import resolve_path
from jevloan.modules import base

MODULE_TITLES = {
    "A": "File readiness",
    "B": "Fraud screen",
    "C": "Appraisal support",
    "D": "Exception triage",
    "E": "Memo and KFS consistency",
    "F": "Post-disbursal monitoring",
}
SAMPLE_BOOK_SIZE = 100
SAMPLE_BOOK_SEED = 7
DEFAULT_RUBRICS_PATH = "docs/RUBRICS.md"


def register(subparsers: "argparse._SubParsersAction") -> None:
    modules = subparsers.add_parser("modules", help="inspect the question packs (questions and rubrics)")
    commands = modules.add_subparsers(dest="modules_command", required=True)

    listing = commands.add_parser("list", help="list every registered question")
    listing.set_defaults(func=_list)

    export = commands.add_parser("rubrics-export", help="write every rubric as Markdown, grouped by module")
    export.add_argument("--out", default=DEFAULT_RUBRICS_PATH, help=f"output path (default {DEFAULT_RUBRICS_PATH})")
    export.set_defaults(func=_export)

    check = commands.add_parser("check", help="check every wire against the PII gate and the rubric-shape rules; exits 1 on failure")
    check.set_defaults(func=_check)


# --- sample states -----------------------------------------------------------------------------------------


def sample_states() -> list[tuple[str, str, dict]]:
    """(stage, segment, state) for a small synthetic book, or [] while the state builder does not exist."""
    if importlib.util.find_spec("jevloan.state") is None:
        return []
    build_state = importlib.import_module("jevloan.state").build_state
    from jevloan.data.generator import generate_book

    out = []
    for loan_file in generate_book(SAMPLE_BOOK_SIZE, SAMPLE_BOOK_SEED):
        for stage in base.STAGES:
            out.append((stage, loan_file.segment, build_state(loan_file, stage)))
    return out


def example_wires(catalog: dict[str, base.QuestionDef], states: list[tuple[str, str, dict]]) -> dict[str, list[dict]]:
    """qid -> wire dicts to check: the static wire, or up to a few distinct wires built from sample states."""
    wires: dict[str, list[dict]] = {}
    for qid, q in catalog.items():
        if q.wire is not None:
            wires[qid] = [q.to_wire({})]
    for stage, segment, state in states:
        for qid, wire in base.questions_for(stage, segment, state).items():
            seen = wires.setdefault(qid, [])
            if catalog[qid].wire is None and wire not in seen and len(seen) < 8:
                seen.append(wire)
    return wires


# --- list --------------------------------------------------------------------------------------------------


def _list(args: argparse.Namespace) -> int:
    catalog = base.catalog()
    rows = [
        (q.qid, q.module, q.stage, q.qtype, ",".join(sorted(q.segments)) if q.segments != base.ALL_SEGMENTS else "all")
        for q in sorted(catalog.values(), key=lambda q: (q.module, q.qid))
    ]
    header = ("qid", "module", "stage", "type", "segments")
    widths = [max(len(str(row[i])) for row in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        print("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip())
    print(f"{len(rows)} questions")
    return 0


# --- rubrics-export ----------------------------------------------------------------------------------------


def _bullets(items: list) -> str:
    return "\n".join(f"  - {item}" for item in items)


def _side_md(name: str, side: dict) -> list[str]:
    lines = [f"- **{name}**: {side['what']}"]
    if side.get("not_for"):
        lines.append(f"  - *Not for*: {side['not_for']}")
    lines.append("  - *Examples*:")
    lines.extend(f"    - {example}" for example in side["examples"])
    return lines


def render_question(q: base.QuestionDef, wire: dict | None) -> str:
    lines = [
        f"### `{q.qid}`",
        "",
        f"- Type: {q.qtype}. Stage: {q.stage}. Segments: {', '.join(sorted(q.segments))}.",
        f"- Risk polarity: {q.risk_polarity}. Routing relevant: {'yes' if q.routing_relevant else 'no'}.",
        f"- Reason text (shown when this question drives an outcome): {q.reason_text}",
        "",
    ]
    if wire is None:
        lines.append("*Built from the state at run time; no sample state was available for this export.*")
        return "\n".join(lines) + "\n"
    instructions = wire["instructions"]
    lines.append(f"**Question.** {instructions['question']}")
    lines.append("")
    if instructions.get("focus"):
        lines += [f"**Focus.** {instructions['focus']}", ""]
    lines.append("**Refers to.** " + ", ".join(instructions["refer_to"]))
    lines.append("")
    if "data" in instructions:
        lines += ["**Data sent with the question.**", "", "```json", json.dumps(instructions["data"], indent=2, ensure_ascii=False), "```", ""]
    criteria = wire["criteria"]
    if wire["type"] == "noul":
        lines.append("**Criteria.**")
        lines += _side_md("Yes (true)", criteria["true"])
        lines += _side_md("No (false)", criteria["false"])
    elif wire["type"] == "score":
        lines.append("**Levels, worst to best.**")
        for index, level in enumerate(criteria):
            lines += _side_md(f"{index}. {level['level']}", level)
    else:
        lines.append("**Options.**")
        for option, spec in criteria.items():
            lines += _side_md(option, spec)
    return "\n".join(lines) + "\n"


def render_rubrics(catalog: dict[str, base.QuestionDef], examples: dict[str, list[dict]]) -> str:
    out = [
        "# Question rubrics",
        "",
        "Generated by `jevloan modules rubrics-export` from the question packs in `src/jevloan/modules/`. Do not edit by hand.",
        "",
        "Every rubric follows the vendor's structured-criteria format: what the answer means, what it is not for, and two or three",
        "examples per side or level, written in the vocabulary of the state (bands, tokens such as `[PAN_1]`, small integers).",
        "A Noul's true side is always a plain yes to the question as worded. A question that depends on the state is shown for a",
        "sample state. The reason text is what the policy engine quotes; it comes from here, never from the model.",
        "",
        f"{len(catalog)} questions.",
        "",
    ]
    for module in sorted({q.module for q in catalog.values()}):
        title = MODULE_TITLES.get(module, module)
        members = sorted((q for q in catalog.values() if q.module == module), key=lambda q: q.qid)
        out += [f"## Module {module}: {title}", ""]
        for q in members:
            wires = examples.get(q.qid)
            out.append(render_question(q, wires[0] if wires else None))
    return "\n".join(out).rstrip() + "\n"


def _export(args: argparse.Namespace) -> int:
    catalog = base.catalog()
    text = render_rubrics(catalog, example_wires(catalog, sample_states()))
    path = resolve_path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote the rubrics of {len(catalog)} questions to {path}")
    return 0


# --- check -------------------------------------------------------------------------------------------------


def _iter_problems(catalog: dict[str, base.QuestionDef], examples: dict[str, list[dict]]) -> Iterator[str]:
    from jevloan.pii.gate import PIIBlocked, PIIGate

    gate = PIIGate()
    for qid, q in sorted(catalog.items()):
        wires = examples.get(qid)
        if not wires:
            continue
        for wire in wires:
            for problem in base.validate_wire(wire):
                yield f"{qid}: rubric shape: {problem}"
            if wire.get("type") != q.qtype:
                yield f"{qid}: wire type {wire.get('type')!r} does not match qtype {q.qtype!r}"
            try:
                gate.check(wire)
            except PIIBlocked as exc:
                yield f"{qid}: PII gate blocked the wire: " + "; ".join(f"{f.detector} at {f.path} ({f.masked})" for f in exc.findings[:5])


def _check(args: argparse.Namespace) -> int:
    catalog = base.catalog()
    states = sample_states()
    examples = example_wires(catalog, states)
    problems = list(_iter_problems(catalog, examples))
    unchecked = sorted(qid for qid in catalog if not examples.get(qid))
    for problem in problems:
        print(f"FAIL {problem}")
    if unchecked:
        print(f"note: {len(unchecked)} state-built question(s) were not exercised (no state builder yet): {', '.join(unchecked)}")
    checked = sum(len(w) for w in examples.values())
    print(f"checked {checked} wire(s) for {len(catalog) - len(unchecked)} of {len(catalog)} questions: {len(problems)} problem(s)")
    return 1 if problems else 0
