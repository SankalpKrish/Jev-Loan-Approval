"""`jevloan api serve`: run the advisory API locally or in guarded production mode."""

from __future__ import annotations

import argparse
import sys

from jevloan.config import ConfigError, load_runtime
from jevloan.policy.config import load_policy, owners_unassigned


def register(subparsers: "argparse._SubParsersAction") -> None:
    api = subparsers.add_parser("api", help="serve the human advisory review API")
    commands = api.add_subparsers(dest="api_command", required=True)
    serve = commands.add_parser("serve", help="run the FastAPI advisory service")
    serve.add_argument("--mode", choices=("dev", "production"), default="dev")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=_serve)


def _serve(args: argparse.Namespace) -> int:
    try:
        runtime = load_runtime()
        if args.mode == "production":
            if runtime.backend == "sim":
                raise ConfigError("production API refuses backend=sim")
            unassigned = owners_unassigned(load_policy())
            if unassigned:
                raise ConfigError(f"production API refuses UNASSIGNED queue owners: {', '.join(unassigned)}")
        import uvicorn
        from jevloan.api.app import create_app

        uvicorn.run(create_app(runtime), host=args.host, port=args.port)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0
