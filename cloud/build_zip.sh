#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dist
rm -f dist/cloud-function.zip
zip -qr dist/cloud-function.zip mail2disk requirements.txt -x '*/__pycache__/*'
(cd cloud && zip -q ../dist/cloud-function.zip index.py)
echo "Done: dist/cloud-function.zip"
