#!/usr/bin/env bash
# Scheduled entry point for the Project Overseer: scripts/run_slot.sh {0900|1230|1630} [extra args]
set -euo pipefail
cd /home/box/project-delta/repo
exec /home/box/project-delta/venv/bin/python scripts/run.py --slot "$1" "${@:2}"
