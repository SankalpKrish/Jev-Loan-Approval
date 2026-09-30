"""``jevloan data generate`` and ``jevloan data stats``."""

from __future__ import annotations

import argparse
import time
from collections import Counter
from collections.abc import Iterable
from typing import Any

from jevloan.config import load_runtime, resolve_path
from jevloan.data.generator import generate_book
from jevloan.data.schema import SEGMENTS, LoanFile, load_book, write_book

DEFAULT_N = 2000
DEFAULT_SEED = 7
DISPARITY_SUBSETS = ("lang_doc_script", "gender_income_proxy", "pincode_bureau_thin")


def register(subparsers: "argparse._SubParsersAction") -> None:
    data = subparsers.add_parser("data", help="generate and inspect the synthetic loan book")
    commands = data.add_subparsers(dest="data_command", required=True)

    gen = commands.add_parser("generate", help="generate a deterministic synthetic book (JSON lines)")
    gen.add_argument("--n", type=int, default=DEFAULT_N, help=f"number of files (default {DEFAULT_N})")
    gen.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"random seed (default {DEFAULT_SEED})")
    gen.add_argument("--out", help="output path (default: book_path from config/runtime.yaml)")
    gen.set_defaults(func=_generate)

    stats = commands.add_parser("stats", help="print base rates, outcomes, disparity subsets and per-group rates")
    stats.add_argument("--book", help="book to read (default: book_path from config/runtime.yaml)")
    stats.set_defaults(func=_stats)


def _generate(args: argparse.Namespace) -> int:
    out = resolve_path(args.out) if args.out else resolve_path(load_runtime().book_path)
    started = time.perf_counter()
    files = generate_book(args.n, args.seed)
    n = write_book(files, out)
    print(f"wrote {n} synthetic files (seed {args.seed}) to {out} in {time.perf_counter() - started:.1f}s")
    return 0


def _stats(args: argparse.Namespace) -> int:
    path = resolve_path(args.book) if args.book else resolve_path(load_runtime().book_path)
    if not path.is_file():
        print(f"no book at {path}; run `jevloan data generate` first")
        return 1
    print(format_stats(compute_stats(load_book(path))))
    return 0


def _rate(hits: int, n: int) -> float:
    return hits / n if n else 0.0


def compute_stats(files: Iterable[LoanFile]) -> dict[str, Any]:
    book = list(files)
    n = len(book)
    stats: dict[str, Any] = {"n": n}
    stats["segments"] = {s: sum(1 for f in book if f.segment == s) for s in SEGMENTS}
    stats["sanctionable"] = _rate(sum(f.labels.sanctionable for f in book), n)
    stats["sanctionable_by_segment"] = {
        s: _rate(sum(f.labels.sanctionable for f in book if f.segment == s), stats["segments"][s]) for s in SEGMENTS
    }
    stats["fraud"] = _rate(sum(f.labels.fraud for f in book), n)
    stats["fraud_types"] = {k: _rate(v, n) for k, v in sorted(Counter(f.labels.fraud_type for f in book if f.labels.fraud_type).items())}
    stats["missing_any"] = _rate(sum(bool(f.labels.missing_items) for f in book), n)
    stats["missing_items"] = {k: _rate(v, n) for k, v in sorted(Counter(m for f in book for m in f.labels.missing_items).items())}
    stats["memo_defect_any"] = _rate(sum(bool(f.labels.memo_defects) for f in book), n)
    kinds = {d.split(":")[0] for f in book for d in f.labels.memo_defects}
    stats["memo_defect_kinds"] = {k: _rate(sum(1 for f in book if any(d.split(":")[0] == k for d in f.labels.memo_defects)), n) for k in sorted(kinds)}
    stats["outcomes"] = {o: _rate(sum(1 for f in book if f.labels.outcome_12m == o), n) for o in ("repays", "slips", "defaults")}
    stats["mean_pd"] = sum(f.labels.risk_pd for f in book) / n if n else 0.0
    stats["closeness"] = {k: _rate(sum(1 for f in book if f.labels.closeness_level == k), n) for k in range(5)}
    stats["weakness"] = {k: _rate(v, n) for k, v in sorted(Counter(f.labels.primary_weakness for f in book).items())}
    stats["disparity"] = {k: sum(1 for f in book if f.meta.disparity_subset == k) for k in DISPARITY_SUBSETS}
    stats["disparity"]["total"] = sum(stats["disparity"].values())
    groups: dict[str, dict[str, dict[str, float]]] = {}
    for attr in ("gender", "pincode_cluster", "language"):
        keys = sorted({getattr(f.demographics, attr) for f in book})
        groups[attr] = {}
        for k in keys:
            sub = [f for f in book if getattr(f.demographics, attr) == k]
            groups[attr][k] = {
                "n": len(sub),
                "sanctionable": _rate(sum(f.labels.sanctionable for f in sub), len(sub)),
                "defaults": _rate(sum(f.labels.outcome_12m == "defaults" for f in sub), len(sub)),
            }
    stats["groups"] = groups
    return stats


def _pct(x: float) -> str:
    return f"{100 * x:5.1f}%"


def format_stats(stats: dict[str, Any]) -> str:
    n = stats["n"]
    lines = [f"Synthetic book: {n} files", "", "Segment mix"]
    for s, c in stats["segments"].items():
        lines.append(f"  {s:<20} {c:>5}  {_pct(_rate(c, n))}   sanctionable {_pct(stats['sanctionable_by_segment'][s])}")
    lines += ["", "Base rates",
              f"  sanctionable           {_pct(stats['sanctionable'])}",
              f"  fraud                  {_pct(stats['fraud'])}   " + ", ".join(f"{k} {_pct(v).strip()}" for k, v in stats["fraud_types"].items()),
              f"  >=1 missing item       {_pct(stats['missing_any'])}   " + ", ".join(f"{k} {_pct(v).strip()}" for k, v in stats["missing_items"].items()),
              f"  >=1 memo defect        {_pct(stats['memo_defect_any'])}   " + ", ".join(f"{k} {_pct(v).strip()}" for k, v in stats["memo_defect_kinds"].items()),
              f"  mean risk_pd           {stats['mean_pd']:.4f}"]
    lines += ["", "12-month outcome: " + ", ".join(f"{k} {_pct(v).strip()}" for k, v in stats["outcomes"].items())]
    lines += ["Closeness level:  " + ", ".join(f"{k} {_pct(v).strip()}" for k, v in stats["closeness"].items())]
    lines += ["Primary weakness: " + ", ".join(f"{k} {_pct(v).strip()}" for k, v in stats["weakness"].items())]
    d = stats["disparity"]
    lines += ["", "Engineered disparity subsets (files, share of book)"]
    for k in DISPARITY_SUBSETS:
        lines.append(f"  {k:<22} {d[k]:>5}  {_pct(_rate(d[k], n))}")
    lines.append(f"  {'total':<22} {d['total']:>5}  {_pct(_rate(d['total'], n))}")
    for attr, table in stats["groups"].items():
        lines += ["", f"By {attr}", f"  {'group':<8} {'n':>5}  {'sanctionable':>12}  {'defaults':>8}"]
        for k, row in table.items():
            lines.append(f"  {k:<8} {row['n']:>5}  {_pct(row['sanctionable']):>12}  {_pct(row['defaults']):>8}")
    return "\n".join(lines)
