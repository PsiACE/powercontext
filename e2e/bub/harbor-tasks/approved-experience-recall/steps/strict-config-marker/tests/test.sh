#!/bin/sh
set -eu

if python - <<'PY'
import json
from pathlib import Path

actual = json.loads(Path("/workspace/config.json").read_text(encoding="utf-8"))
expected = {"mode": "strict", "marker": "POWERCONTEXT_STRICT_V2"}
raise SystemExit(0 if actual == expected else 1)
PY
then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
