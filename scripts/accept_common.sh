#!/usr/bin/env bash
set -euo pipefail

ACCEPT_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ACCEPT_ROOT="$(cd -- "$ACCEPT_SCRIPT_DIR/.." && pwd)"
ACCEPT_PY="$ACCEPT_ROOT/.venv/bin/python"

if [[ ! -x "$ACCEPT_PY" ]]; then
  echo "missing project interpreter: $ACCEPT_PY" >&2
  exit 2
fi

ACCEPT_TMP="$(mktemp -d "${TMPDIR:-/tmp}/jevloan-accept.XXXXXX")"
trap 'rm -rf -- "$ACCEPT_TMP"' EXIT

jevloan() {
  PYTHONPATH="$ACCEPT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$ACCEPT_PY" -m jevloan "$@"
}

echo "acceptance workspace: $ACCEPT_TMP"
