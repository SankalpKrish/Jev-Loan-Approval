"""`jevloan <group> <command>`. Each command module exposes `register(subparsers)`, which adds its
group and sets `func` on each command's parser; `func(args)` returns the process exit code (or None for 0)."""

import argparse
import importlib
import importlib.util
from collections.abc import Sequence

COMMAND_MODULES = [
    "jevloan.audit.commands",
    "jevloan.jev.commands",
    "jevloan.pii.commands",
    "jevloan.data.commands",
    "jevloan.state.commands",
    "jevloan.modules.commands",
    "jevloan.pipeline.commands",
    "jevloan.eval.commands",
    "jevloan.api.commands",
    "jevloan.monitors.commands",
]


def _is_installed(module_name: str) -> bool:
    """True if the module exists. A module (or parent package) that is simply absent is not an error;
    anything else that goes wrong while importing a parent package is."""
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if module_name == missing or module_name.startswith(missing + "."):
            return False
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jevloan", description="Loan appraisal support on TypeSafe Jev. Advisory only: humans decide."
    )
    subparsers = parser.add_subparsers(dest="group", required=True, metavar="<group>")
    for module_name in COMMAND_MODULES:
        if _is_installed(module_name):
            importlib.import_module(module_name).register(subparsers)  # import errors must crash
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)
