"""CLI commands for replaying books and checking audit trail completeness."""

from __future__ import annotations

import argparse
import asyncio

from jevloan.audit.log import AuditLog
from jevloan.config import load_runtime, resolve_path
from jevloan.data.schema import load_book
from jevloan.pipeline.replay import replay
from jevloan.pipeline.trail import trail_complete


def register(subparsers: "argparse._SubParsersAction") -> None:
    group = subparsers.add_parser("pipeline", help="run offline pipeline replay and inspect audit trails")
    commands = group.add_subparsers(dest="pipeline_command", required=True)

    run = commands.add_parser("replay", help="replay every file through all three stages")
    run.add_argument("--book", help="synthetic book path (default from runtime config)")
    run.add_argument("--limit", type=int, help="process only the first N files")
    run.add_argument("--concurrency", type=int, help="maximum files processed concurrently")
    run.add_argument("--run-id", help="reproducible run identifier (generated when omitted)")
    run.add_argument("--runs-dir", default="data/runs", help="root directory for replay artifacts")
    run.add_argument("--db", help="audit database path (default from runtime config)")
    run.set_defaults(func=_replay)

    check = commands.add_parser("check-trails", help="check requested stage trails for completeness")
    check.add_argument("--book", help="book whose file ids should be checked (default from runtime config)")
    check.add_argument("--file-id", action="append", help="check only this file id; may be repeated")
    check.add_argument(
        "--stages", default="appraisal,sanction_docs,monitoring",
        help="comma-separated expected stage attempts; duplicates represent repeated attempts",
    )
    check.add_argument("--db", help="audit database path (default from runtime config)")
    check.set_defaults(func=_check_trails)


def _runtime(args: argparse.Namespace):
    runtime = load_runtime()
    if args.db:
        runtime = runtime.model_copy(update={"db_path": args.db})
    return runtime


def _replay(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    book = args.book or runtime.book_path
    summary = asyncio.run(
        replay(
            book,
            runtime,
            limit=args.limit,
            concurrency=args.concurrency,
            run_id=args.run_id,
            runs_dir=args.runs_dir,
        )
    )
    print(f"replayed {summary.files} files across {len(summary.stages)} stages ({summary.decisions} decisions)")
    print(f"run id: {summary.run_id}")
    print(f"decisions: {summary.decisions_path}")
    print(f"metadata: {summary.metadata_path}")
    print(f"audit database: {summary.audit_db_path}")
    return 0


def _check_trails(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    stages = tuple(stage.strip() for stage in args.stages.split(",") if stage.strip())
    if not stages:
        print("no stages requested")
        return 2
    if args.file_id:
        file_ids = list(dict.fromkeys(args.file_id))
    else:
        book = args.book or runtime.book_path
        file_ids = [file.file_id for file in load_book(book)]
    audit = AuditLog(resolve_path(runtime.db_path))
    incomplete = 0
    for file_id in file_ids:
        ok, problems = trail_complete(audit, file_id, stages)
        if not ok:
            incomplete += 1
            print(f"INCOMPLETE {file_id}: " + "; ".join(problems))
    total = len(file_ids)
    complete = total - incomplete
    print(f"trails complete: {complete}/{total}")
    return 1 if incomplete else 0
