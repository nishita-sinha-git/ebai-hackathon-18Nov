#!/usr/bin/env bash
# Install the built wheel into a clean virtualenv and run the smoke suite from
# outside the source tree, so the check exercises what users actually get.
set -euo pipefail

PY="${PY:-python3}"
WHEEL="$(ls "$PWD"/dist/*.whl | head -n 1)"
REPORT="$PWD/smoke-report.txt"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

"$PY" -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q "$WHEEL"

cd "$WORK"
# Guard: the import must resolve to the installed package, never to the source tree.
"$WORK/venv/bin/python" -c "
import app
path = app.__file__
assert 'site-packages' in path, f'imported from source tree: {path}'
print('imported from', path)
"
"$WORK/venv/bin/python" -m app.smoke | tee "$REPORT"
