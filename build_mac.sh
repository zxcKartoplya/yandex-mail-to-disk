#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
VENV=.venv-build
rm -rf "$VENV"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install -q -r requirements-dev.txt
"$VENV/bin/python" -m pytest -q
"$VENV/bin/pyinstaller" --onefile --console --clean --noconfirm --specpath build --workpath build --name YandexMailToDisk "$PWD/run.py"
echo
echo "Done: dist/YandexMailToDisk ($(uname -m)), requires macOS $(otool -l dist/YandexMailToDisk | awk '/minos/{print $2; exit}')+"
