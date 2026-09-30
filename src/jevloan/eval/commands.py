"""`jevloan eval run`: measure a replay against its book and frozen thresholds."""

from __future__ import annotations

import argparse
import json
import sys

from jevloan.eval.core import evaluate_run


def register(subparsers: "argparse._SubParsersAction") -> None:
    group = subparsers.add_parser("eval", help="evaluate a replay and write metrics, disagreements and a decision memo")
    commands = group.add_subparsers(dest="eval_command", required=True)
    run = commands.add_parser("run", help="evaluate data/runs/<run-id>")
    run.add_argument("--run-id", required=True)
    run.add_argument("--split", choices=("holdout", "dev", "all"), default="holdout")
    run.add_argument("--runs-dir", default="data/runs")
    run.add_argument("--book-path", help="override the replay metadata book path")
    run.add_argument("--reports-dir", default="reports")
    run.add_argument("--thresholds", default="config/parity_thresholds.yaml")
    run.add_argument("--memo-template", default="docs/GO_NO_GO_MEMO_TEMPLATE.md")
    run.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    try:
        result = evaluate_run(args.run_id, split=args.split, runs_dir=args.runs_dir, book_path=args.book_path,
                              reports_dir=args.reports_dir, thresholds_path=args.thresholds, memo_template=args.memo_template)
    except (OSError, ValueError, KeyError) as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"run_id": result["run_id"], "split": result["split"], "recommendation": result["recommendation"],
                      "report_dir": str(args.reports_dir.rstrip("/") + "/" + args.run_id)}, sort_keys=True))
    return 0
