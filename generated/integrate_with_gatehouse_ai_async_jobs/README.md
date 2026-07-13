# Job-Star

Intelligent client for gatehouse-ai async jobs. Job-Star decides what to
execute, when, and how, acting as the orchestration layer on top of
gatehouse-ai's async job interface.

## Install (dev)


// --- DUPLICATE BLOCK ---

# Submit a job
job-star submit index-repo --param repo=https://github.com/example/repo --priority 5

# Check status
job-star status <job-id>

# List jobs
job-star list --status running --limit 10

# Cancel
job-star cancel <job-id>
