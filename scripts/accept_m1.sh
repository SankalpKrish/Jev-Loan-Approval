#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/accept_common.sh"

export JEVLOAN_BACKEND=sim
jevloan data generate --n 2000 --seed 7 --out "$ACCEPT_TMP/book.jsonl"
jevloan data stats --book "$ACCEPT_TMP/book.jsonl"
jevloan pii fixtures-check
jevloan modules check
jevloan state stats --book "$ACCEPT_TMP/book.jsonl"
jevloan state leak-scan --book "$ACCEPT_TMP/book.jsonl"
echo "M1 data, question-pack, state-size, and PII acceptance passed. This does not establish model quality."
