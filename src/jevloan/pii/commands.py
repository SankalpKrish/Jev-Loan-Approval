"""`jevloan pii fixtures-check` and `jevloan pii scan PATH`."""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from jevloan.pii.fixtures import adversarial_fixtures, clean_samples, known_gap_fixtures
from jevloan.pii.gate import PIIGate


def register(subparsers: argparse._SubParsersAction) -> None:
    pii = subparsers.add_parser("pii", help="PII gate: adversarial fixture check and file scans")
    commands = pii.add_subparsers(dest="pii_command", required=True)

    check = commands.add_parser("fixtures-check", help="catch rate on adversarial fixtures and false-positive rate on clean samples; exits 1 unless the catch rate is 100 percent and the false-positive rate is 0 percent")
    check.add_argument("--verbose", "-v", action="store_true", help="list every miss and every false positive")
    check.set_defaults(func=_fixtures_check)

    scan = commands.add_parser("scan", help="scan a JSON or JSONL file for personal identifiers; exits 1 if anything is found")
    scan.add_argument("path", help="a .json file (one payload) or .jsonl file (one payload per line)")
    scan.set_defaults(func=_scan)


def _fixtures_check(args: argparse.Namespace) -> int:
    gate = PIIGate()
    adversarial = adversarial_fixtures()
    clean = clean_samples()

    per_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # category -> [caught, total]
    misses: list[str] = []
    wrong: list[str] = []
    started = time.perf_counter()
    for fx in adversarial:
        findings = gate.scan(fx.payload)
        per_category[fx.category][1] += 1
        if not findings:
            misses.append(f"{fx.id} [{fx.category}] expected {sorted(fx.expected_detectors)}")
            continue
        fired = {f.detector for f in findings}
        if fx.expected_detectors and not (fired & fx.expected_detectors):
            wrong.append(f"{fx.id} [{fx.category}] expected {sorted(fx.expected_detectors)}, got {sorted(fired)}")
            continue
        per_category[fx.category][0] += 1
    false_positives: list[str] = []
    for i, sample in enumerate(clean):
        findings = gate.scan(sample)
        if findings:
            false_positives.append(f"clean[{i}]: " + ", ".join(f"{f.detector}@{f.path} ({f.masked})" for f in findings[:4]))
    elapsed_ms = (time.perf_counter() - started) * 1000

    caught = sum(v[0] for v in per_category.values())
    total = len(adversarial)
    catch_rate = 100.0 * caught / total if total else 0.0
    fp_rate = 100.0 * len(false_positives) / len(clean) if clean else 0.0

    width = max(len(c) for c in per_category)
    print(f"{'category':<{width}}  caught/total")
    for cat, (ok, n) in per_category.items():
        print(f"{cat:<{width}}  {ok}/{n}{'' if ok == n else '   <-- MISSED'}")
    print()
    print(f"adversarial fixtures : {caught}/{total} caught  (catch rate {catch_rate:.1f}%)")
    print(f"clean samples        : {len(false_positives)}/{len(clean)} flagged  (false-positive rate {fp_rate:.1f}%)")
    print(f"scan time            : {elapsed_ms:.0f} ms for {total + len(clean)} payloads")
    gaps = known_gap_fixtures()
    if gaps:
        print()
        print(f"known limitations ({len(gaps)} classes, not counted above):")
        for g in gaps:
            status = "still uncaught" if not gate.scan(g.payload) else "now caught"
            print(f"  {g.id} {g.category} [{status}]: {g.note}")
    if args.verbose or misses or wrong or false_positives:
        for line in misses:
            print("MISS   ", line)
        for line in wrong:
            print("WRONG  ", line)
        for line in false_positives:
            print("FALSE+ ", line)
    ok = catch_rate == 100.0 and fp_rate == 0.0 and not wrong
    print()
    print("PASS" if ok else "FAIL: the gate must catch 100% of adversarial fixtures and 0% of clean samples")
    return 0 if ok else 1


def _load(path: Path) -> list[tuple[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [(f"line {i}", json.loads(line)) for i, line in enumerate(text.splitlines(), 1) if line.strip()]
    return [("document", json.loads(text))]


def _scan(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"no such file: {path}")
        return 2
    gate = PIIGate()
    records = _load(path)
    hits = 0
    for label, payload in records:
        for f in gate.scan(payload):
            hits += 1
            print(f"{label}: {f.detector} at {f.path} ({f.masked})")
    print(f"scanned {len(records)} record(s): {hits} finding(s)")
    return 1 if hits else 0
