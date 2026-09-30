#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/accept_common.sh"

"$ACCEPT_PY" -m pytest -q \
  "$ACCEPT_ROOT"/tests/test_api*.py \
  "$ACCEPT_ROOT/tests/chaos/test_api_failures.py" \
  "$ACCEPT_ROOT/tests/test_jev_chaos.py" \
  "$ACCEPT_ROOT/tests/test_jev_gateway.py" \
  "$ACCEPT_ROOT/tests/test_pipeline_responsiveness.py"
echo "M4 API and chaos test acceptance passed."
