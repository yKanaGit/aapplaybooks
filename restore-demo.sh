#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec python3 -B rhel-patch-demo-restore/restore_demo.py "$@"
