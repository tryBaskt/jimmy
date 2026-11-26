#!/usr/bin/env bash
# Preserve Terraform's exit status and expose actionable failures in check annotations.
set -euo pipefail
diagnostics=$(mktemp)
trap 'rm -f "$diagnostics"' EXIT
if terraform "$@" 2>"$diagnostics"; then
  cat "$diagnostics" >&2
else
  status=$?
  cat "$diagnostics" >&2
  python3 - "$diagnostics" <<'PY'
from pathlib import Path
import sys
message = Path(sys.argv[1]).read_text(errors="replace")[-6000:]
message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
print(f"::error title=Terraform provisioning failed::{message}")
PY
  exit "$status"
fi
