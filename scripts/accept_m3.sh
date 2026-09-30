#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/accept_common.sh"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 RUN_ID [eval options, for example --split all]" >&2
  exit 2
fi
RUN_ID="$1"
shift
RUNS_DIR="${JEVLOAN_RUNS_DIR:-data/runs}"
if [[ "$RUNS_DIR" = /* ]]; then
  RUNS_BASE="$RUNS_DIR"
else
  RUNS_BASE="$ACCEPT_ROOT/$RUNS_DIR"
fi
METADATA="$RUNS_BASE/$RUN_ID/metadata.json"
if [[ ! -f "$METADATA" ]]; then
  echo "replay metadata not found: $METADATA" >&2
  exit 2
fi
BACKEND="$("$ACCEPT_PY" - "$METADATA" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream).get("backend", "unknown"))
PY
)"
echo "evaluating supplied replay $RUN_ID (recorded backend: $BACKEND); this script never starts a replay or makes a model call"
jevloan eval run --run-id "$RUN_ID" --runs-dir "$RUNS_DIR" --reports-dir "${JEVLOAN_REPORTS_DIR:-reports}" "$@"
