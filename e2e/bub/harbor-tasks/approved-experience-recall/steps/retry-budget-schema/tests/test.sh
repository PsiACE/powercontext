#!/bin/sh
set -eu

if python - <<'PY'
import json
from pathlib import Path

actual = json.loads(Path("/workspace/retry.json").read_text(encoding="utf-8"))
expected = {"retry_budget_ms": 3000}
raise SystemExit(0 if actual == expected else 1)
PY
then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
