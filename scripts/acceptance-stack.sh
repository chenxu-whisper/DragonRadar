#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# State is data, never shell input. Python handles JSON and protected-file checks.
exec python3 -B "${ROOT_DIR}/scripts/acceptance_stack.py" "$@"
