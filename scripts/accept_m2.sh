#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/accept_common.sh"

export JEVLOAN_BACKEND=sim
ACCEPT_N="${JEVLOAN_ACCEPTANCE_N:-2000}"
jevloan data generate --n "$ACCEPT_N" --seed 7 --out "$ACCEPT_TMP/book.jsonl"
jevloan pipeline replay \
  --book "$ACCEPT_TMP/book.jsonl" \
  --limit "$ACCEPT_N" \
  --concurrency "${JEVLOAN_ACCEPTANCE_CONCURRENCY:-16}" \
  --run-id "accept-m2" \
  --runs-dir "$ACCEPT_TMP/runs" \
  --db "$ACCEPT_TMP/audit.sqlite3"
jevloan pipeline check-trails --book "$ACCEPT_TMP/book.jsonl" --db "$ACCEPT_TMP/audit.sqlite3"
jevloan audit verify --db "$ACCEPT_TMP/audit.sqlite3"
echo "M2 simulator replay acceptance passed on $ACCEPT_N file(s). This does not establish real Jev quality."
