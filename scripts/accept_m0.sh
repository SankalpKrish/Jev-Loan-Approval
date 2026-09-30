#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/accept_common.sh"

export JEVLOAN_BACKEND=sim
jevloan jev hello --backend sim --db "$ACCEPT_TMP/audit.sqlite3"
jevloan audit verify --db "$ACCEPT_TMP/audit.sqlite3"
echo "M0 simulator and audit acceptance passed. This does not establish real Jev quality."
