#!/bin/bash
# Job-Star panel launcher on nexus
cd /home/craig/job-star/job-star || { echo "cd failed"; sleep 5; exit 1; }
export PYTHONPATH=/home/craig/job-star/job-star
echo "Starting job-star panel..."
/home/craig/job-star/job-star/.venv/bin/python -m job_star.panel --interval 3 2>/tmp/panel-err.log
RC=$?
echo ""
echo "Panel exited with code $RC"
cat /tmp/panel-err.log 2>/dev/null
echo ""
echo "Press Enter to close..."
read -r