#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pytest -q
python3 -m landlordshare "$@"
echo; echo "Artifacts in data/processed/:"; ls -1 data/processed/
