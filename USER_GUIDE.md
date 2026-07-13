# Job-Star — User Guide

## The Mental Model

Job-star manages **goals**. A goal is anything you want done — a bug fix, a
research topic, a learning plan, a personal task. You give job-star a goal,
and it breaks it into **steps**, executes them with AI, and reports back.

```
You add a goal
    ↓
Job-star plans it into steps (via AI)
    ↓
Workers execute steps one at a time (using free/cheap AI models)
    ↓
You get a check-in when there's something to review or decide
    ↓
You respond → job-star continues (or completes the goal)
```

Everything is tracked in a Postgres database. Workers run continuously in the
background. You interact via the CLI, the web UI, or email/chat notifications.

---

## The Daily Workflow

### 1. Check the dashboard
```bash
python3 -m job_star
```
Shows you: what needs your attention, what's happening, what to do next.

### 2. Respond to check-ins
```bash
python3 -m job_star review
```
Walks through check-ins that need your response. Or click the link in the
email/chat notification — it opens a web page where you can discuss with an
AI helper before deciding.

### 3. Get an AI summary
```bash
python3 -m job_star commentary
```
A natural-language narrative of what the system has done and what it's doing.

---

## Adding Goals

```bash
# Add a goal — job-star will plan and execute it
python3 -m job_star add "Fix the login bug on the settings page"

# With details and priority
python3 -m job_star add "Research WiFi 7 adoption trends" \
    --desc "Track industry adoption, be ready for Q3 planning" \
    --urgency idle-opportunistic \
    --domain personal
```

**Urgency** controls how aggressively it's worked on:
- `imperative` — do it now, best model
- `soon` — background, decent model
- `idle-opportunistic` — only when free models are available
- `timed` — deadline-aware (future)

**Domain** is just a label for grouping: `coding`, `personal`, `infra`, `meta`.

---

## Managing Goals

```bash
python3 -m job_star list                 # See all goals
python3 -m job_star list --status active # Only active goals
python3 -m job_star show <id>            # Details + steps for a goal
python3 -m job_star work <id>            # Start/continue work on a goal
python3 -m job_star complete <id>        # Mark a goal done manually
```

Goal IDs can be abbreviated — `job_star show abc123` works if only one goal
starts with `abc123`.

---

## Check-Ins

Check-ins are how job-star asks for your input. There are four types:

| Type | When | What it asks |
|------|------|-------------|
| 📊 **Progress** | Every 7 days + 3 steps | "Here's what I've done. Any direction changes?" |
| ❓ **Clarification** | When stuck | "I'm not sure about X. What do you want?" |
| 🏁 **Milestone** | At phase boundaries | "Phase 1 done. Does this match expectations?" |
| ✅ **Completion** | When all steps done | "Accept this result, or does it need revision?" |

**You'll get notified via email and Google Chat** with a link to a web page.
On the page, you can chat with an AI helper (Gemini) about the check-in before
responding.

```bash
# CLI alternative
python3 -m job_star checkin pending              # What needs response
python3 -m job_star checkin show <id>            # See a check-in
python3 -m job_star checkin respond <id> \
    --answer "1" --feedback "Looks good, proceed"
```

---

## How It Works Under the Hood

### Workers
Workers are background processes that continuously claim and execute steps.
Multiple workers can run, each specialized:
- **Generic worker** — handles unowned goals
- **Expert workers** — own specific codebases (gatehouse-ai, job-star itself)
- **Research worker** — handles recurring monitoring/learning goals

Workers communicate through Postgres (`FOR UPDATE SKIP LOCKED`), so they never
collide. You can run workers on multiple machines — they all pull from the same
queue.

### Models
Job-star only uses **free or cheap models** by default (quota-free tier from
the gatehouse gateway). Expensive models (Claude Opus, etc.) require explicit
permission. The cost so far: **$0.00**.

### Safety
- Every action is logged to an audit trail
- Workers can't exceed budget limits
- Goals complete only when you accept the completion check-in
- Code changes to job-star itself go through PR review + the upgrade tool

---

## Common Questions

**"Why isn't my goal making progress?"**
Check if it has steps: `job_star show <id>`. If it has no steps, run
`job_star work <id>` to plan and start it. If steps are pending but nothing's
happening, check the dashboard — workers might be idle or the gateway might
be down.

**"How do I stop a goal?"**
```bash
python3 -m job_star complete <id>    # Mark it done
# or set status to abandoned via the DB
```

**"What if I want a specific AI model?"**
```bash
python3 -m job_star work <id> --model glm-5-2
```
Only free/cheap models work by default. For expensive models, you'd need to
pass `allow_expensive=True` (not exposed in CLI yet).

**"Where's the data?"**
- Database: `postgresql://jobstar:jobstar@localhost:5432/job_star`
- Tickle files (research output): `~/tickle-file/`
- Audit trail: in the database, view with `job_star digest`

---

## Quick Command Reference

| Command | What it does |
|---------|-------------|
| `job_star` | Dashboard — what's happening |
| `job_star review` | Respond to pending check-ins |
| `job_star commentary` | AI summary of the system |
| `job_star add "title"` | Add a new goal |
| `job_star list` | See all goals |
| `job_star show <id>` | Goal details + steps |
| `job_star work <id>` | Start/continue a goal |
| `job_star checkin pending` | What needs your response |
| `job_star status` | System health + model tiers |
| `job_star upgrade` | Deploy code changes safely |
| `job_star panel` | Live terminal dashboard |

Run `job_star help` for the full reference.