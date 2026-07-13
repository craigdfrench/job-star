#!/bin/bash
# Ensure a persistent tmux session runs the job-star panel.
# The panel restarts inside the session if it crashes.
SESSION=job-star-panel

if tmux has-session -t "$SESSION" 2>/dev/null; then
  exit 0
fi

tmux new-session -d -s "$SESSION" \
  "while true; do cd /home/craig/job-star/job-star && PYTHONPATH=/home/craig/job-star/job-star /home/craig/job-star/job-star/.venv/bin/python -m job_star.panel --interval 3; echo '--- panel exited, restarting in 2s ---'; sleep 2; done"

echo "tmux session '$SESSION' started"