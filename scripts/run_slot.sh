#!/usr/bin/env bash
# Scheduled entry point for the Project Overseer: scripts/run_slot.sh {0900|1230|1630} [extra args]
set -euo pipefail
# Host check (run.py repeats it): refuse to run anywhere but the Overseer box.
if [ "$(hostname)" != "grok-bot-vm-429005534" ] || [ "$(cat /etc/machine-id 2>/dev/null)" != "be992e2cea5940209955b1d96b858ea2" ] || [ ! -d /home/box/project-delta/repo/.git ]; then
  echo "HOST CHECK FAILED: run_slot.sh may only run on the Overseer box (grok-bot-vm-429005534) in /home/box/project-delta/repo" >&2
  exit 3
fi
cd /home/box/project-delta/repo
# Serialize runs so a manual and a scheduled run can never book the same slot twice.
exec flock -w 900 /tmp/project-delta-run.lock /home/box/project-delta/venv/bin/python scripts/run.py --slot "$1" "${@:2}"
