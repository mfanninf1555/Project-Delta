#!/usr/bin/env bash
# Scheduled entry point for the Project Overseer: scripts/run_slot.sh {0900|1230|1630} [extra args]
set -euo pipefail
cd /home/box/project-delta/repo
# Serialize runs so a manual and a scheduled run can never book the same slot twice.
exec flock -w 900 /tmp/project-delta-run.lock /home/box/project-delta/venv/bin/python scripts/run.py --slot "$1" "${@:2}"
