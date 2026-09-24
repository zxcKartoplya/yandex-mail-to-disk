#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/pyinstaller --onefile --console --clean --noconfirm --specpath build --workpath build --name YandexMailToDisk "$PWD/run.py"
echo
echo "Done: dist/YandexMailToDisk ($(uname -m))"
