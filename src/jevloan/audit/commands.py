"""`jevloan audit verify` and `jevloan audit dump`."""

import argparse
import json
from pathlib import Path

from jevloan.audit.log import JSON_COLUMNS, AuditLog
from jevloan.config import load_runtime, resolve_path


def register(subparsers: "argparse._SubParsersAction") -> None:
    audit = subparsers.add_parser("audit", help="verify or export the audit log")
    commands = audit.add_subparsers(dest="audit_command", required=True)

    verify = commands.add_parser("verify", help="check the hash chain; exits 1 if it is broken")
    verify.add_argument("--db", help="audit database (default: db_path from config/runtime.yaml)")
    verify.set_defaults(func=_verify)

    dump = commands.add_parser("dump", help="export one file's complete trail as JSON")
    dump.add_argument("--file-id", required=True)
    dump.add_argument("--db", help="audit database (default: db_path from config/runtime.yaml)")
    dump.add_argument("--out", help="write here instead of stdout")
    dump.set_defaults(func=_dump)


def _open(args: argparse.Namespace) -> AuditLog:
    return AuditLog(args.db if args.db else resolve_path(load_runtime().db_path))


def _verify(args: argparse.Namespace) -> int:
    result = _open(args).verify()
    if result.ok:
        print(f"OK: audit chain verified, {result.entries} entries")
        return 0
    print(f"FAILED: audit chain broken at seq {result.first_bad_seq}: {result.reason} ({result.entries} entries)")
    return 1


def _dump(args: argparse.Namespace) -> int:
    log = _open(args)
    entries = [
        {k: json.loads(v) if k in JSON_COLUMNS and v is not None else v for k, v in entry.items()}
        for entry in log.trail(args.file_id)
    ]
    text = json.dumps(
        {"file_id": args.file_id, "entries": entries, "chain_verified": log.verify().ok},
        indent=2,
        ensure_ascii=False,
    )
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {len(entries)} entries for {args.file_id} to {args.out}")
    else:
        print(text)
    return 0
