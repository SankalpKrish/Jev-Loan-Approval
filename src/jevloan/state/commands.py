"""``jevloan state show | stats | leak-scan``.

* ``show``: pretty-print one state.
* ``stats``: token-estimate distribution per segment x stage; exits 1 unless the appraisal mean is under 3000 and
  the appraisal p99 under 3500 (the M1 requirement).
* ``leak-scan``: for every file and stage, (a) search every raw value of the file's ``pii_inventory`` (normalised,
  case-folded, digit-compacted) in the normalised state and (b) run ``PIIGate.scan`` on the state. Exits 1 on any
  hit or finding. Only masked values are ever printed.

With no ``--book`` the book is generated on the fly (2,000 files, seed 7), except for ``show``, which uses the
configured book file if it exists.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from jevloan.canonical import canonical_json
from jevloan.config import load_runtime, resolve_path
from jevloan.data.generator import generate_book, generate_file
from jevloan.data.schema import SEGMENTS, LoanFile, load_book
from jevloan.pii.detectors import mask, mask_name
from jevloan.pii.gate import PIIGate
from jevloan.pii.normalize import compact_digits, normalize
from jevloan.state import STAGES, build_state, redactor_for
from jevloan.state.tokens import estimate_tokens

DEFAULT_N = 2000
DEFAULT_SEED = 7
MEAN_LIMIT = 3000  # M1: appraisal states average under 3k tokens ...
P99_LIMIT = 3500  # ... and the 99th percentile stays under 3.5k
MAX_EXAMPLES = 20
MIN_NEEDLE = 4  # inventory values shorter than this (after normalising) are ignored
MIN_NAME_PART = 3  # so are whole-word name parts shorter than this

_INVENTORY_KINDS = (
    "person_names", "org_names", "pans", "aadhaars", "phones", "emails", "account_numbers", "address_lines", "pincodes",
)  # fmt: skip


def register(subparsers: "argparse._SubParsersAction") -> None:
    state = subparsers.add_parser("state", help="build, measure and leak-scan the PII-free model states")
    commands = state.add_subparsers(dest="state_command", required=True)

    show = commands.add_parser("show", help="pretty-print the state of one file")
    show.add_argument("--file-id", required=True, help="e.g. F000123")
    show.add_argument("--stage", default="appraisal", choices=STAGES)
    show.add_argument("--book", help="book to read (default: the configured book file, else generated on the fly)")
    show.set_defaults(func=_show)

    stats = commands.add_parser("stats", help="token-estimate distribution per segment and stage (M1 gate)")
    stats.add_argument("--book", help=f"book to read (default: generate {DEFAULT_N} files, seed {DEFAULT_SEED})")
    stats.set_defaults(func=_stats)

    scan = commands.add_parser("leak-scan", help="search every state for the file's raw identifiers (M1 gate)")
    scan.add_argument("--book", help=f"book to read (default: generate {DEFAULT_N} files, seed {DEFAULT_SEED})")
    scan.set_defaults(func=_leak_scan)


# ------------------------------------------------------------------------------------------------ book access


def _load_book(book: str | None) -> list[LoanFile]:
    if book:
        path = resolve_path(book)
        if not path.is_file():
            raise SystemExit(f"no book at {path}")
        return list(load_book(path))
    print(f"no --book given: generating {DEFAULT_N} files with seed {DEFAULT_SEED}", file=sys.stderr)
    return generate_book(DEFAULT_N, DEFAULT_SEED)


def _find_file(file_id: str, book: str | None) -> LoanFile:
    if book:
        path = resolve_path(book)
        if not path.is_file():
            raise SystemExit(f"no book at {path}")
        return _pick(load_book(path), file_id)
    configured = resolve_path(load_runtime().book_path)
    if configured.is_file():
        return _pick(load_book(configured), file_id)
    print(f"no book at {configured}: generating the file (seed {DEFAULT_SEED})", file=sys.stderr)
    m = re.fullmatch(r"F(\d{6})", file_id)
    if m and 1 <= int(m.group(1)) <= DEFAULT_N:
        return generate_file(int(m.group(1)) - 1, DEFAULT_SEED)
    raise SystemExit(f"{file_id!r} is not a file id of the generated book (F000001 to F{DEFAULT_N:06d})")


def _pick(files: Iterable[LoanFile], file_id: str) -> LoanFile:
    for f in files:
        if f.file_id == file_id:
            return f
    raise SystemExit(f"no file {file_id} in the book")


# ------------------------------------------------------------------------------------------------ show


def _show(args: argparse.Namespace) -> int:
    file = _find_file(args.file_id, args.book)
    state = build_state(file, args.stage)
    print(json.dumps(state, indent=2, ensure_ascii=False))
    print(f"# {file.segment} / {args.stage}: about {estimate_tokens(state)} tokens", file=sys.stderr)
    return 0


# ------------------------------------------------------------------------------------------------ stats


def _dist(values: list[int]) -> dict[str, float]:
    a = np.asarray(values, dtype=float)
    return {
        "n": len(values), "mean": float(a.mean()), "p50": float(np.percentile(a, 50)),
        "p95": float(np.percentile(a, 95)), "p99": float(np.percentile(a, 99)), "max": float(a.max()),
    }  # fmt: skip


def compute_token_stats(files: Iterable[LoanFile]) -> dict:
    """``{"by": {(segment, stage): [tokens]}, "appraisal": [tokens]}`` for every file and stage."""
    by: dict[tuple[str, str], list[int]] = {}
    for f in files:
        r = redactor_for(f)
        for stage in STAGES:
            by.setdefault((f.segment, stage), []).append(estimate_tokens(build_state(f, stage, redactor=r)))
    appraisal = [t for (seg, stage), ts in by.items() if stage == "appraisal" for t in ts]
    return {"by": by, "appraisal": appraisal}


def format_token_stats(stats: dict) -> tuple[str, bool]:
    lines = [f"{'segment':<18} {'stage':<14} {'n':>5} {'mean':>7} {'p50':>7} {'p95':>7} {'p99':>7} {'max':>7}"]
    for seg in SEGMENTS:
        for stage in STAGES:
            ts = stats["by"].get((seg, stage))
            if ts:
                d = _dist(ts)
                lines.append(
                    f"{seg:<18} {stage:<14} {d['n']:>5} {d['mean']:>7.0f} {d['p50']:>7.0f} {d['p95']:>7.0f} {d['p99']:>7.0f} {d['max']:>7.0f}"
                )
    d = _dist(stats["appraisal"])
    ok = d["mean"] < MEAN_LIMIT and d["p99"] < P99_LIMIT
    lines.append("")
    lines.append(
        f"appraisal, all segments: n={d['n']} mean={d['mean']:.0f} p99={d['p99']:.0f} "
        f"(limits: mean < {MEAN_LIMIT}, p99 < {P99_LIMIT}) -> {'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines), ok


def _stats(args: argparse.Namespace) -> int:
    files = _load_book(args.book)
    started = time.perf_counter()
    text, ok = format_token_stats(compute_token_stats(files))
    print(f"State token estimates (canonical JSON characters / 2.2), {len(files)} files, {time.perf_counter() - started:.0f}s")
    print(text)
    return 0 if ok else 1


# ------------------------------------------------------------------------------------------------ leak-scan


@dataclass(frozen=True)
class InventoryHit:
    kind: str
    masked: str


def _squash(s: str) -> str:
    return " ".join(s.split())


def _fold(s: str) -> str:
    """normalised, case-folded, whitespace-collapsed."""
    return _squash(normalize(s).casefold())


def _needles(file: LoanFile) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(substring needles, whole-word name parts) as (kind, folded value). Needles are also digit-compacted."""
    inv = file.pii_inventory
    values: dict[str, set[str]] = {kind: set(getattr(inv, kind)) for kind in _INVENTORY_KINDS}
    values["aliases"] = set(inv.aliases) | set(inv.aliases.values())  # alias keys and their canonical values
    if file.applicant.name_native:
        values["person_names"].add(file.applicant.name_native)
    needles: set[tuple[str, str]] = set()
    for kind, vs in values.items():
        for v in vs:
            folded = compact_digits(_fold(v)).replace(" ", "") if kind in ("pans", "aadhaars", "phones", "account_numbers") else compact_digits(_fold(v))
            if len(folded) >= MIN_NEEDLE:
                needles.add((kind, folded))
    parts: set[tuple[str, str]] = set()
    for name in values["person_names"]:
        for part in re.findall(r"\w+", _fold(name)):
            if len(part) >= MIN_NAME_PART and not part.isdigit():
                parts.add(("person_name_part", part))
    return sorted(needles), sorted(parts)


def inventory_hits(file: LoanFile, state: dict) -> list[InventoryHit]:
    """(a) every raw inventory value that appears in the normalised canonical JSON of ``state``."""
    text = _fold(canonical_json(state))
    compact = compact_digits(text)
    squashed = compact.replace(" ", "")
    needles, parts = _needles(file)
    hits: list[InventoryHit] = []
    for kind, needle in needles:
        found = needle in squashed if kind in ("pans", "aadhaars", "phones", "account_numbers") else needle in compact
        if found:
            hits.append(InventoryHit(kind, mask(needle)))
    for kind, part in parts:
        if re.search(rf"(?<!\w){re.escape(part)}(?!\w)", text):
            hits.append(InventoryHit(kind, mask_name(part)))
    return hits


def leak_scan(files: Iterable[LoanFile]) -> dict:
    gate = PIIGate()
    result = {"files": 0, "states": 0, "a_hits": [], "b_findings": [], "a_by_kind": Counter(), "b_by_detector": Counter()}
    for f in files:
        result["files"] += 1
        r = redactor_for(f)
        for stage in STAGES:
            state = build_state(f, stage, redactor=r)
            result["states"] += 1
            for h in inventory_hits(f, state):
                result["a_by_kind"][h.kind] += 1
                result["a_hits"].append((f.file_id, stage, h.kind, h.masked))
            for finding in gate.scan(state):
                result["b_by_detector"][finding.detector] += 1
                result["b_findings"].append((f.file_id, stage, finding.detector, finding.path, finding.masked))
    return result


def format_leak_scan(result: dict) -> tuple[str, bool]:
    a, b = result["a_hits"], result["b_findings"]
    lines = [
        f"leak-scan: {result['files']} files x {len(STAGES)} stages = {result['states']} states",
        f"(a) raw inventory values found in a state: {len(a)} hits"
        + (f" ({', '.join(f'{k} {v}' for k, v in sorted(result['a_by_kind'].items()))})" if a else ""),
        f"(b) PIIGate findings: {len(b)}"
        + (f" ({', '.join(f'{k} {v}' for k, v in sorted(result['b_by_detector'].items()))})" if b else ""),
    ]
    for file_id, stage, kind, masked in a[:MAX_EXAMPLES]:
        lines.append(f"  (a) {file_id} {stage}: {kind} {masked}")
    for file_id, stage, detector, path, masked in b[:MAX_EXAMPLES]:
        lines.append(f"  (b) {file_id} {stage}: {detector} at {path} ({masked})")
    ok = not a and not b
    lines.append("PASS: no hits, no findings" if ok else "FAIL")
    return "\n".join(lines), ok


def _leak_scan(args: argparse.Namespace) -> int:
    files = _load_book(args.book)
    started = time.perf_counter()
    text, ok = format_leak_scan(leak_scan(files))
    print(text)
    print(f"({time.perf_counter() - started:.0f}s)", file=sys.stderr)
    return 0 if ok else 1
