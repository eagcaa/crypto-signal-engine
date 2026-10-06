#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v python3.12 >/dev/null 2>&1; then
  echo "python3.12 is required but was not found."
  exit 1
fi

if [ ! -d ".venv" ]; then
  python3.12 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"

echo
echo "Setup complete."
echo "Run:"
echo "  source .venv/bin/activate"
echo "  make test"
echo "  make db-up"
echo "  make live"
