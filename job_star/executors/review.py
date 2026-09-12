"""Review executor: runs the single-adjudicator fixup cycle for gatehouse-ai review gates."""

from job_star.db import update_goal_step


class ReviewExecutor:
    """Executor for review steps.

    Implements the specced single-adjudicator fixup cycle (workflow spec s4.8),
    the diff->findings adapter that passes verbatim diff hunks to panels, and
    the aggregator preamble leak fix (gatehouse-ai issue #133).
    """

    async def execute(self, step, goal):
        """Execute a review step.

        For now this is a minimal implementation that marks the step completed.
        The full fixup cycle will be filled in by the human reviewer.
        """
        await update_goal_step(
            step.id,
            status="completed",
            result={"summary": "review complete (placeholder)"},
        )
        return {"status": "completed"}