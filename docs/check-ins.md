# Job-Star Check-In System

## Structured Two-Way Progress Dialogue

---

## Overview

The check-in system creates a **structured, two-way conversation** between job-star and the user at key moments in a goal's lifecycle. Unlike the follow-up engine (reactive, event-driven) or the ask/answer API (ad-hoc), check-ins are **scheduled, AI-generated, persistent, and actionable**.

A check-in is a deliberate checkpoint where the system:
1. Summarizes progress (what's been done)
2. Presents results (what was produced)
3. Asks specific questions (where user input would improve the outcome)
4. Waits for the user's response
5. Acts on the response (accept, redirect, revise, clarify)

---

## Check-In Types

| Type | Icon | When it triggers | Purpose |
|------|------|-----------------|---------|
| **Progress** | 📊 | After every N completed steps (default 3) | "Here's what I've done, here's what's next, here's where I need your input." |
| **Clarification** | ❓ | When a step fails 2+ times | "I'm stuck on X. Which direction should I take?" |
| **Milestone** | 🏁 | On demand (manually triggered) | "Phase 1 is done. Here are the results. Does this match what you expected?" |
| **Completion** | ✅ | When all steps are complete (before auto-completing the goal) | "The goal is complete. Do you accept this, or does it need revision?" |

---

## Lifecycle

```
draft → sent → awaiting_response → responded → actioned
                                                       ↓
                                                    expired
```

- **draft**: Created but not yet sent (used during AI generation)
- **sent**: Delivered to the user, waiting for response
- **responded**: User has provided feedback and/or answers
- **actioned**: The system has processed the user's response and taken action
- **expired**: User never responded (future feature)

---

## How Check-Ins Are Generated

Check-ins are AI-generated. When a check-in is triggered:

1. The system gathers completed step results, failed attempts, and pending steps
2. Sends all of this to an AI model (free/cheap tier) with a structured prompt
3. The AI returns a JSON object with:
   - `progress_summary`: 2-4 sentence summary of what's been accomplished
   - `next_steps`: 1-2 sentence description of what's planned
   - `results`: Key deliverables (for milestone/completion only)
   - `questions`: Array of structured questions for the user

4. If AI generation fails, a fallback check-in is created from raw step data

### Question types

| Type | Description | Example |
|------|-------------|---------|
| `choice` | Discrete options | "Which approach? A) REST B) GraphQL" |
| `text` | Open-ended | "Any specific requirements?" |
| `approval` | Accept/reject | "Do you accept this result? Accept / Needs revision" |
| `rating` | Numerical rating | Future use |

---

## How to Use

### CLI

```bash
# List all check-ins
python3 -m job_star checkin list

# List check-ins for a specific goal
python3 -m job_star checkin list --goal <goal-id>

# List only pending check-ins (awaiting your response)
python3 -m job_star checkin pending

# Show a check-in with full details
python3 -m job_star checkin show <checkin-id>

# Create a check-in for a goal
python3 -m job_star checkin create <goal-id> --type progress
python3 -m job_star checkin create <goal-id> --type completion

# Respond to a check-in
python3 -m job_star checkin respond <checkin-id> --feedback "Looks good, proceed."
python3 -m job_star checkin respond <checkin-id> --answer "1"  # answer first option
python3 -m job_star checkin respond <checkin-id> --answer qid="Accept" --feedback "Ship it."
```

### API

```bash
# List check-ins
GET /api/v1/check-ins?status=sent

# Get a single check-in
GET /api/v1/check-ins/{id}

# Create a check-in for a goal
POST /api/v1/goals/{goal_id}/check-in
  Body: {"type": "progress"}

# Respond to a check-in
POST /api/v1/check-ins/{id}/respond
  Body: {
    "response": "free-text feedback",
    "decisions": [{"question_id": "abc123", "answer": "Accept"}]
  }
```

---

## Automatic Triggers

The orchestrator automatically creates check-ins at these points:

### After step completion (progress)
```python
# In orchestrator.work_on_goal(), after a step completes:
check_in = await self.checkin_engine.maybe_create_progress_check_in(goal, all_steps)
```
- Triggers after every N completed steps (default N=3)
- N is configurable per goal: `goal.metadata.check_in_interval = 5`
- Only triggers if enough NEW steps have completed since the last check-in

### After repeated step failures (clarification)
```python
# In orchestrator.work_on_goal(), when a step fails:
if len(failed_steps) >= 2:
    check_in = await self.checkin_engine.create_clarification_check_in(
        goal, all_steps, step=step, issue=f"Step '{step.title}' failed: {error}"
    )
```

### When all steps complete (completion)
```python
# In orchestrator.work_on_goal(), when progress reaches 100%:
if await should_create_completion_check_in(goal, all_steps):
    check_in = await self.checkin_engine.create_completion_check_in(goal, all_steps)
```
- Instead of auto-completing the goal, a completion check-in is created
- The goal is only marked completed when the user accepts the result
- If the user says "Needs revision", the goal stays open

---

## Response Processing

When the user responds, the system processes the answer:

### Completion check-ins
- **"Accept"** → goal is marked as completed
- **"Needs revision"** → goal stays open, user feedback is recorded in the decisions table

### All check-ins
- Free-text feedback is recorded in the decisions table with shadow paths
- Question answers are saved in the check-in's `questions` JSONB
- The check-in status transitions: `sent → responded → actioned`
- An event is published to the SSE event bus (`checkin.responded`)

---

## Database Schema

```sql
CREATE TABLE check_ins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    step_id UUID REFERENCES goal_steps(id) ON DELETE SET NULL,

    type TEXT NOT NULL DEFAULT 'progress',      -- progress, clarification, milestone, completion
    status TEXT NOT NULL DEFAULT 'draft',       -- draft, sent, awaiting_response, responded, actioned, expired

    progress_summary TEXT,     -- AI-generated summary of progress
    next_steps TEXT,           -- what's planned next
    results TEXT,              -- key deliverables for review
    questions JSONB DEFAULT '[]',  -- [{id, question, type, options[], required, answer}]

    response TEXT,             -- user's free-text feedback
    decisions JSONB DEFAULT '[]',  -- [{question_id, answer}]
    responded_at TIMESTAMP,

    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## File Locations

| File | Purpose |
|------|---------|
| `job_star/checkin/__init__.py` | Models: `CheckIn`, `CheckInType`, `CheckInStatus`, `CheckInQuestion` |
| `job_star/checkin/engine.py` | `CheckInEngine`: AI generation, trigger logic, response processing, DB CRUD |
| `job_star/orchestrator.py` | Triggers check-ins after step completion, failure, and goal completion |
| `job_star/cli.py` | `checkin` command: list, show, pending, create, respond |
| `job_star/api/routes.py` | API endpoints for check-ins |
| `sql/schema.sql` | `check_ins` table definition |
| `sql/migrations/002_check_ins.sql` | Migration that added the table |