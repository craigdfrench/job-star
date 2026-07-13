"""Core worker logic shared by CLI and the worker package.

Runs continuously, claims pending steps from the Postgres queue using
`FOR UPDATE SKIP LOCKED`, and executes them via the orchestrator.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

from .db import claim_next_step_any_goal, claim_job_queue_item, complete_job, close_pool, publish_event
from .models import Domain, Urgency
from .orchestrator import Orchestrator


class Worker:
    """Standalone worker that executes steps from the shared job queue."""

    def __init__(
        self,
        worker_id: Optional[str] = None,
        worker_machine: Optional[str] = None,
        urgency: Optional[str] = None,
        domain: Optional[str] = None,
        expert: Optional[str] = None,
        expert_any: bool = False,
        interval: float = 30.0,
        max_cycles: Optional[int] = None,
        model: Optional[str] = None,
    ):
        self.worker_id = worker_id or os.environ.get("JOB_STAR_WORKER") or os.environ.get("HOSTNAME", "worker")
        self.worker_machine = worker_machine or os.environ.get("JOB_STAR_MACHINE") or os.environ.get("HOSTNAME", "")
        self.urgency = Urgency(urgency) if urgency else None
        self.domain = Domain(domain) if domain else None
        self.expert = expert or os.environ.get("JOB_STAR_EXPERT")
        self.expert_any = expert_any
        self.interval = interval
        self.max_cycles = max_cycles
        self.model = model
        self.orch = Orchestrator()

    async def _process_job_queue(self) -> bool:
        """Claim and process a job_queue item (e.g., plan a new goal)."""
        job = await claim_job_queue_item(
            self.worker_id,
            expert=self.expert,
            expert_any=self.expert_any,
        )
        if not job:
            return False

        goal_id = str(job["goal_id"])
        kind = job["kind"]
        payload = job.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload) if payload else {}
        elif not isinstance(payload, dict):
            payload = {}
        model = payload.get("model") or self.model

        print(f"  [{self.worker_id}] claimed job {job['id']}: {kind} for goal {goal_id[:8]}", flush=True)

        try:
            if kind == "plan":
                # Ensure the goal is planned before the step queue takes over
                steps = await self.orch.plan_goal(goal_id)
                await publish_event("goal.planned", {"goal_id": goal_id, "job_id": str(job["id"]), "step_count": len(steps)})

            # If steps were created, continue to execute the next one
            result = await self.orch.work_on_goal(goal_id, model_override=model)
            if result.success:
                print(f"  [{self.worker_id}] done: goal {goal_id[:8]} [{result.model}]", flush=True)
            else:
                print(f"  [{self.worker_id}] failed: goal {goal_id[:8]}: {result.error[:60] if result.error else 'unknown'}", flush=True)
                await complete_job(str(job["id"]), "failed")
                return True

            await complete_job(str(job["id"]), "completed")
            return True
        except Exception as exc:
            print(f"  [{self.worker_id}] job {job['id']} error: {exc}", flush=True)
            await complete_job(str(job["id"]), "failed")
            await publish_event("job.failed", {"job_id": str(job["id"]), "goal_id": goal_id, "error": str(exc)})
            return True

    async def run_once(self) -> bool:
        """Claim and execute one work unit (job_queue or pending step)."""
        # Prefer job_queue items first (plan/execute requests from API)
        if await self._process_job_queue():
            return True

        # Otherwise claim a pending step from any goal
        claimed = await claim_next_step_any_goal(
            urgency=self.urgency,
            domain=self.domain,
            expert=self.expert,
            expert_any=self.expert_any,
            worker_machine=self.worker_machine,
        )
        if not claimed:
            return False

        goal, step = claimed
        expert_tag = f" [{goal.expert}]" if goal.expert else ""
        print(f"  [{self.worker_id}] claimed:{expert_tag} {goal.title[:40]} → {step.title[:40]}", flush=True)

        result = await self.orch.work_on_goal(goal.id, model_override=self.model)
        if result.success:
            print(f"  [{self.worker_id}] done: {step.title[:40]} [{result.model}]", flush=True)
        else:
            print(f"  [{self.worker_id}] failed: {result.error[:60] if result.error else 'unknown'}", flush=True)
        return True

    async def run(self) -> None:
        """Run the worker loop until max_cycles or interrupted."""
        print(f"  Worker '{self.worker_id}' started. interval={self.interval}s", flush=True)
        print(f"  Machine: {self.worker_machine or '(unknown)'}", flush=True)
        if self.urgency:
            print(f"  urgency filter: {self.urgency.value}", flush=True)
        if self.domain:
            print(f"  domain filter: {self.domain.value}", flush=True)
        if self.expert:
            print(f"  expert affinity: {self.expert}", flush=True)
        else:
            print(f"  expert affinity: generic (unowned goals only)", flush=True)
        if self.model:
            print(f"  model override: {self.model}", flush=True)
        print(flush=True)

        cycle = 0
        try:
            while True:
                if self.max_cycles and cycle >= self.max_cycles:
                    print(f"  Worker '{self.worker_id}' finished after {cycle} cycles.", flush=True)
                    break
                cycle += 1

                did_work = await self.run_once()
                if not did_work:
                    print(f"  [{self.worker_id}] no work available, sleeping {self.interval}s...", flush=True)
                    await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            print(f"  [{self.worker_id}] cancelled.", flush=True)
            raise
        finally:
            await close_pool()


async def main() -> None:
    """CLI entry point for `python -m job_star.worker`."""
    import argparse

    parser = argparse.ArgumentParser(description="Job-Star worker")
    parser.add_argument("--urgency", choices=[u.value for u in Urgency], help="Filter by urgency")
    parser.add_argument("--domain", choices=[d.value for d in Domain], help="Filter by domain")
    parser.add_argument("--expert", help="Expert affinity")
    parser.add_argument("--expert-any", action="store_true", help="Claim any expert goal")
    parser.add_argument("--interval", type=float, default=30.0, help="Sleep seconds when no work")
    parser.add_argument("--cycles", type=int, default=None, help="Max cycles (default: infinite)")
    parser.add_argument("--model", help="Override model selection")
    parser.add_argument("--worker-id", help="Worker identifier")
    parser.add_argument("--worker-machine", help="Machine name")
    args = parser.parse_args()

    worker = Worker(
        worker_id=args.worker_id,
        worker_machine=args.worker_machine,
        urgency=args.urgency,
        domain=args.domain,
        expert=args.expert,
        expert_any=args.expert_any,
        interval=args.interval,
        max_cycles=args.cycles,
        model=args.model,
    )
    await worker.run()
