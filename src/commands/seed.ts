/**
 * Job-Star CLI Commands
 *
 * Each command is a function that takes parsed args and executes against
 * the goal registry (Postgres) and optionally the AI layer.
 *
 * Seed commands:
 *   add      — Add a goal to the registry
 *   list     — List all goals (with filters)
 *   show     — Show a goal's details and its steps
 *   step     — Add a step to a goal
 *   work     — Trigger AI to work on a goal's next pending step
 *   complete — Mark a goal or step as completed
 *   digest   — Show recent audit trail events
 */

import { query, queryOne, audit, type GoalRow, type StepRow } from '../db.ts';
import { callAI, type AICallResult } from '../ai.ts';
import { parsePlanOutput } from '../planParser.ts';

// ============================================================================
// ADD — Create a new goal
// ============================================================================

export async function cmdAdd(args: string[]): Promise<void> {
  // Parse: job-star add "title" [--domain coding] [--urgency soon] [--desc "description"]
  const parsed = parseFlags(args, {
    domain: 'coding',
    urgency: 'soon',
    desc: '',
    parent: '',
  });

  const title = parsed.positional[0];
  if (!title) {
    console.error('Usage: job-star add "title" [--domain coding] [--urgency soon] [--desc "..."]');
    process.exit(1);
  }

  const row = await queryOne<GoalRow>(
    `INSERT INTO goals (title, description, domain, urgency, source, metadata)
     VALUES ($1, $2, $3, $4, 'user', '{}')
     RETURNING *`,
    [
      title,
      parsed.flags.desc || null,
      parsed.flags.domain,
      parsed.flags.urgency,
    ]
  );

  if (parsed.flags.parent) {
    await query(
      `UPDATE goals SET parent_id = $2 WHERE id = $1`,
      [row!.id, parsed.flags.parent]
    );
  }

  await audit('goal_created', { title, domain: parsed.flags.domain, urgency: parsed.flags.urgency }, row!.id);

  console.log(`✦ Goal created`);
  console.log(`  ID:       ${row!.id}`);
  console.log(`  Title:    ${row!.title}`);
  console.log(`  Domain:   ${row!.domain}`);
  console.log(`  Urgency:  ${row!.urgency}`);
  console.log(`  Status:   ${row!.status}`);
  console.log();
  console.log(`  Add steps:  job-star step ${row!.id} "First step description"`);
  console.log(`  Work on it: job-star work ${row!.id}`);
}

// ============================================================================
// LIST — List all goals
// ============================================================================

export async function cmdList(args: string[]): Promise<void> {
  const parsed = parseFlags(args, {
    status: '',
    domain: '',
    urgency: '',
  });

  let sql = 'SELECT * FROM goals';
  const conditions: string[] = [];
  const params: unknown[] = [];
  let paramIdx = 1;

  if (parsed.flags.status) {
    conditions.push(`status = $${paramIdx++}`);
    params.push(parsed.flags.status);
  }
  if (parsed.flags.domain) {
    conditions.push(`domain = $${paramIdx++}`);
    params.push(parsed.flags.domain);
  }
  if (parsed.flags.urgency) {
    conditions.push(`urgency = $${paramIdx++}`);
    params.push(parsed.flags.urgency);
  }

  if (conditions.length > 0) {
    sql += ' WHERE ' + conditions.join(' AND ');
  }
  sql += ' ORDER BY CASE urgency WHEN \'imperative\' THEN 0 WHEN \'soon\' THEN 1 WHEN \'idle-opportunistic\' THEN 2 ELSE 3 END, updated_at DESC';

  const goals = await query<GoalRow>(sql, params);

  if (goals.length === 0) {
    console.log('  No goals found. Add one with: job-star add "title"');
    return;
  }

  // Group by urgency for display
  const byUrgency: Record<string, GoalRow[]> = {};
  for (const g of goals) {
    if (!byUrgency[g.urgency]) byUrgency[g.urgency] = [];
    byUrgency[g.urgency].push(g);
  }

  const urgencyOrder = ['imperative', 'soon', 'idle-opportunistic', 'timed'];
  const urgencyIcon: Record<string, string> = {
    imperative: '🔴',
    soon: '🟡',
    'idle-opportunistic': '🟢',
    timed: '⏰',
  };

  for (const urgency of urgencyOrder) {
    const items = byUrgency[urgency];
    if (!items || items.length === 0) continue;

    console.log(`\n  ${urgencyIcon[urgency]}  ${urgency.toUpperCase()}`);
    console.log(`  ${'─'.repeat(60)}`);

    for (const g of items) {
      const id = g.id.substring(0, 8);
      const status = g.status === 'active' ? '  ' : `[${g.status}] `;
      const progress = g.progress > 0 ? ` (${Math.round(g.progress * 100)}%)` : '';
      console.log(`  ${status}${id}  ${g.title}${progress}`);
    }
  }
  console.log();
  console.log(`  Total: ${goals.length} goals`);
  console.log();
  console.log(`  Show details:  job-star show <id>`);
  console.log(`  Work on one:   job-star work <id>`);
}

// ============================================================================
// SHOW — Show a goal's details and its steps
// ============================================================================

export async function cmdShow(args: string[]): Promise<void> {
  const goalId = args[0];
  if (!goalId) {
    console.error('Usage: job-star show <goal-id>');
    process.exit(1);
  }

  // Support partial UUID
  const fullId = await resolveGoalId(goalId);

  const goal = await queryOne<GoalRow>(
    'SELECT * FROM goals WHERE id = $1',
    [fullId]
  );

  if (!goal) {
    console.error(`Goal not found: ${goalId}`);
    process.exit(1);
  }

  const steps = await query<StepRow>(
    'SELECT * FROM goal_steps WHERE goal_id = $1 ORDER BY order_index',
    [fullId]
  );

  const conflicts = await query(
    `SELECT * FROM goal_conflicts WHERE (goal_a_id = $1 OR goal_b_id = $1) AND resolution = 'unresolved'`,
    [fullId]
  );

  console.log();
  console.log(`  ┌─────────────────────────────────────────────────`);
  console.log(`  │ ${goal.title}`);
  console.log(`  └─────────────────────────────────────────────────`);
  console.log();
  if (goal.description) {
    console.log(`  Description:  ${goal.description}`);
    console.log();
  }
  console.log(`  ID:           ${goal.id}`);
  console.log(`  Domain:       ${goal.domain}`);
  console.log(`  Urgency:      ${goal.urgency}`);
  console.log(`  Status:       ${goal.status}`);
  console.log(`  Progress:     ${Math.round(goal.progress * 100)}%`);
  if (goal.blockers.length > 0) {
    console.log(`  Blockers:     ${goal.blockers.join(', ')}`);
  }
  if (goal.deadline) {
    console.log(`  Deadline:     ${goal.deadline}`);
  }
  console.log(`  Created:      ${goal.created_at}`);
  console.log(`  Updated:      ${goal.updated_at}`);
  console.log();

  // Show steps
  if (steps.length === 0) {
    console.log(`  No steps yet. Add one:`);
    console.log(`    job-star step ${goal.id.substring(0, 8)} "Step description"`);
  } else {
    console.log(`  STEPS:`);
    console.log();
    for (const s of steps) {
      const icon = s.status === 'completed' ? '✓' :
                   s.status === 'in_progress' ? '◉' :
                   s.status === 'failed' ? '✗' :
                   s.status === 'blocked' ? '⊘' : '○';
      const id = s.id.substring(0, 8);
      const model = s.model ? ` [${s.model}]` : '';
      console.log(`    ${icon} ${id}  ${s.title}${model}`);
      if (s.description) {
        console.log(`        ${s.description}`);
      }
    }
  }

  // Show conflicts
  if (conflicts.length > 0) {
    console.log();
    console.log(`  ⚠  CONFLICTS DETECTED:`);
    for (const c of conflicts) {
      const otherId = c.goal_a_id === fullId ? c.goal_b_id : c.goal_a_id;
      console.log(`    ${c.conflict_type}: ${otherId.substring(0, 8)} — ${c.description || '(no description)'}`);
    }
  }

  console.log();
  console.log(`  Work on it:   job-star work ${goal.id.substring(0, 8)}`);
  console.log();
}

// ============================================================================
// STEP — Add a step to a goal
// ============================================================================

export async function cmdStep(args: string[]): Promise<void> {
  const parsed = parseFlags(args, { desc: '' });
  const goalId = parsed.positional[0];
  const title = parsed.positional[1];

  if (!goalId || !title) {
    console.error('Usage: job-star step <goal-id> "step title" [--desc "description"]');
    process.exit(1);
  }

  const fullId = await resolveGoalId(goalId);

  // Get next order_index
  const maxOrder = await queryOne<{ max: number }>(
    'SELECT COALESCE(MAX(order_index), 0) as max FROM goal_steps WHERE goal_id = $1',
    [fullId]
  );

  const nextIndex = (maxOrder?.max || 0) + 1;

  const step = await queryOne<StepRow>(
    `INSERT INTO goal_steps (goal_id, title, description, order_index)
     VALUES ($1, $2, $3, $4)
     RETURNING *`,
    [fullId, title, parsed.flags.desc || null, nextIndex]
  );

  await audit('step_created', { title, order_index: nextIndex }, fullId, step!.id);

  console.log(`✦ Step added to goal`);
  console.log(`  Step ID:  ${step!.id}`);
  console.log(`  Title:    ${step!.title}`);
  console.log(`  Order:    ${nextIndex}`);
  console.log();
  console.log(`  Work on it: job-star work ${goalId}`);
}

// ============================================================================
// WORK — Trigger AI to work on a goal's next pending step
// ============================================================================

export async function cmdWork(args: string[]): Promise<void> {
  const goalId = args[0];
  if (!goalId) {
    console.error('Usage: job-star work <goal-id>');
    process.exit(1);
  }

  const fullId = await resolveGoalId(goalId);

  const goal = await queryOne<GoalRow>(
    'SELECT * FROM goals WHERE id = $1',
    [fullId]
  );

  if (!goal) {
    console.error(`Goal not found: ${goalId}`);
    process.exit(1);
  }

  // Find the next pending step
  let step = await queryOne<StepRow>(
    `SELECT * FROM goal_steps WHERE goal_id = $1 AND status = 'pending' ORDER BY order_index LIMIT 1`,
    [fullId]
  );

  // If no steps exist yet, ask the AI to plan AND auto-save the steps
  if (!step) {
    const steps = await query<StepRow>(
      'SELECT * FROM goal_steps WHERE goal_id = $1 ORDER BY order_index',
      [fullId]
    );

    if (steps.length === 0) {
      console.log(`  No steps yet. Asking AI to plan...`);
      console.log();

      const planPrompt = buildPlanPrompt(goal);
      const result = await callAI(planPrompt.system, planPrompt.user);

      await audit('ai_called', {
        purpose: 'plan',
        input_tokens: result.inputTokens,
        output_tokens: result.outputTokens,
      }, fullId, undefined, result.model);

      // Parse the AI's plan into steps and save them
      const parsedSteps = parsePlanOutput(result.content);

      if (parsedSteps.length === 0) {
        console.log(`  AI Response (${result.model}):`);
        console.log(`  ${'─'.repeat(60)}`);
        console.log(result.content);
        console.log(`  ${'─'.repeat(60)}`);
        console.log();
        console.log(`  ⚠  Could not parse steps from AI output.`);
        console.log(`  Add steps manually:`);
        console.log(`    job-star step ${goal!.id.substring(0, 8)} "Step 1"`);
        return;
      }

      // Save the parsed steps
      console.log(`  AI planned ${parsedSteps.length} steps (using ${result.model}):`);
      console.log();
      for (let i = 0; i < parsedSteps.length; i++) {
        const ps = parsedSteps[i];
        const orderIndex = i + 1;
        const stepRow = await queryOne<StepRow>(
          `INSERT INTO goal_steps (goal_id, title, description, order_index)
           VALUES ($1, $2, $3, $4)
           RETURNING *`,
          [fullId, ps.title, ps.description || null, orderIndex]
        );
        await audit('step_created', { title: ps.title, auto_planned: true }, fullId, stepRow!.id);

        const icon = '○';
        const desc = ps.description ? ` — ${ps.description.substring(0, 60)}${ps.description.length > 60 ? '...' : ''}` : '';
        console.log(`    ${icon} Step ${orderIndex}: ${ps.title}${desc}`);
      }
      console.log();
      console.log(`  Input tokens:  ${result.inputTokens}`);
      console.log(`  Output tokens: ${result.outputTokens}`);
      console.log();
      console.log(`  Steps saved. Now executing first step...`);
      console.log(`  ${'─'.repeat(60)}`);
      console.log();

      // Re-fetch the first pending step (now that we've saved steps)
      step = await queryOne<StepRow>(
        `SELECT * FROM goal_steps WHERE goal_id = $1 AND status = 'pending' ORDER BY order_index LIMIT 1`,
        [fullId]
      );
      // Fall through to execution below
      if (!step) {
        console.log(`  No pending steps after planning. Something went wrong.`);
        return;
      }
    } else {

    // All steps are done
    const allDone = steps.length > 0 && steps.every(s => s.status === 'completed');
    if (allDone) {
      console.log(`  All steps are already completed! 🎉`);
      console.log(`  Goal: ${goal.title}`);
      console.log(`  Progress: ${Math.round(goal.progress * 100)}%`);
      console.log();
      console.log(`  Mark the goal as complete:`);
      console.log(`    job-star complete ${goal!.id.substring(0, 8)}`);
      return;
    }

    console.log(`  No pending steps. Current step statuses:`);
    for (const s of steps) {
      console.log(`    ${s.status}: ${s.title}`);
    }
      return;
    }
  }

  // Mark step as in progress
  await query(
    `UPDATE goal_steps SET status = 'in_progress', attempted_at = NOW() WHERE id = $1`,
    [step.id]
  );

  await audit('step_started', { title: step.title }, fullId, step.id);

  console.log(`  Working on: ${goal.title}`);
  console.log(`  Step:       ${step.title}`);
  if (step.description) {
    console.log(`  Details:     ${step.description}`);
  }
  console.log(`  ${'─'.repeat(60)}`);
  console.log();

  // Build the prompt
  // Query previous completed steps for context
  const prevSteps = await query<StepRow>(
    `SELECT * FROM goal_steps WHERE goal_id = $1 AND status = 'completed' AND order_index < $2 ORDER BY order_index`,
    [fullId, step.order_index]
  );
  const previousSteps: PreviousStep[] = prevSteps.map(s => ({
    title: s.title,
    model: s.model,
    resultContent: ((s.result as { content?: string })?.content) || '',
  }));

  const workPrompt = buildWorkPrompt(goal, step, previousSteps);
  const result = await callAI(workPrompt.system, workPrompt.user);

  // Update step with result
  await query(
    `UPDATE goal_steps
     SET status = 'completed', result = $2, model = $3,
         input_tokens = $4, output_tokens = $5, cost = $6,
         completed_at = NOW()
     WHERE id = $1`,
    [step.id, JSON.stringify({ content: result.content }), result.model,
     result.inputTokens, result.outputTokens, 0]
  );

  await audit('step_completed', {
    title: step.title,
    input_tokens: result.inputTokens,
    output_tokens: result.outputTokens,
  }, fullId, step.id, result.model);

  // Update goal progress
  const steps = await query<StepRow>(
    'SELECT * FROM goal_steps WHERE goal_id = $1 ORDER BY order_index',
    [fullId]
  );
  const completedCount = steps.filter(s => s.status === 'completed').length;
  const newProgress = completedCount / steps.length;

  await query(
    `UPDATE goals SET progress = $2 WHERE id = $1`,
    [fullId, newProgress]
  );

  if (newProgress >= 1.0) {
    await query(
      `UPDATE goals SET status = 'completed' WHERE id = $1`,
      [fullId]
    );
    await audit('goal_completed', {}, fullId);
    console.log(`  🎉 Goal completed!`);
  }

  console.log(`  AI Response (${result.model}):`);
  console.log();
  console.log(result.content);
  console.log();
  console.log(`  ${'─'.repeat(60)}`);
  console.log(`  Step:      ${step.title} → completed`);
  console.log(`  Progress:  ${Math.round(newProgress * 100)}%`);
  console.log(`  Tokens:    ${result.inputTokens} in / ${result.outputTokens} out`);
  console.log();
  if (newProgress < 1.0) {
    console.log(`  Next step:  job-star work ${goal!.id.substring(0, 8)}`);
  }
  console.log();
}

// ============================================================================
// COMPLETE — Mark a goal or step as completed
// ============================================================================

export async function cmdComplete(args: string[]): Promise<void> {
  const id = args[0];
  if (!id) {
    console.error('Usage: job-star complete <goal-id>');
    process.exit(1);
  }

  // Try as goal first
  let fullId = await resolveGoalId(id).catch(() => null);

  if (fullId) {
    await query(
      `UPDATE goals SET status = 'completed', progress = 1.0 WHERE id = $1`,
      [fullId]
    );
    await audit('goal_completed', { manually: true }, fullId);
    console.log(`✦ Goal completed: ${id}`);
    return;
  }

  console.error(`Goal not found: ${id}`);
  process.exit(1);
}

// ============================================================================
// DIGEST — Show recent audit trail
// ============================================================================

export async function cmdDigest(args: string[]): Promise<void> {
  const limit = parseInt(args[0] || '20', 10);

  const events = await query(
    `SELECT a.*, g.title as goal_title
     FROM audit_trail a
     LEFT JOIN goals g ON a.goal_id = g.id
     ORDER BY a.timestamp DESC
     LIMIT $1`,
    [limit]
  );

  if (events.length === 0) {
    console.log('  No events yet. The system is quiet.');
    return;
  }

  console.log();
  console.log(`  RECENT ACTIVITY (last ${limit} events)`);
  console.log(`  ${'─'.repeat(70)}`);
  console.log();

  for (const e of events) {
    const time = new Date(e.timestamp).toLocaleString();
    const goalInfo = e.goal_title ? `  ${e.goal_title.substring(0, 40)}` : '';
    const costInfo = e.cost > 0 ? `  $${e.cost.toFixed(4)}` : '';
    const modelInfo = e.model ? `  [${e.model}]` : '';
    console.log(`  ${time}  ${e.event}${modelInfo}${costInfo}${goalInfo}`);
  }
  console.log();
}

// ============================================================================
// HELP — Show usage
// ============================================================================

export function cmdHelp(): void {
  console.log(`
  Job-Star v0.0.1 — Constrained, supervised, goal-oriented AI orchestration
  
  USAGE:
    job-star <command> [args] [flags]

  COMMANDS:
    add "title"                Add a goal to the registry
      --domain <d>               coding | personal | infra | meta (default: coding)
      --urgency <u>              imperative | soon | idle-opportunistic | timed (default: soon)
      --desc "description"       Longer description
      --parent <id>              Parent goal ID

    list [--status <s>]         List all goals (optionally filtered)
         [--domain <d>]
         [--urgency <u>]

    show <id>                    Show goal details and steps

    step <goal-id> "title"       Add a step to a goal
      --desc "description"

    work <id>                    Auto-plan + execute next step
                                 (plans automatically if no steps exist)
                                 (auto-plans if no steps exist)

    complete <id>                Mark a goal as completed

    digest [N]                   Show last N audit events (default: 20)

    help                         Show this help

  ENVIRONMENT:
    GATEHOUSE_API_URL          Gatehouse-AI endpoint (no key needed)
                                 e.g. http://gatehouse-ai.craigdfrench.com/v1
    GATEHOUSE_API_KEY          Optional key for gatehouse (default: no-key-needed)
    ANTHROPIC_API_KEY            Claude API key
    OPENAI_API_KEY               OpenAI API key
    OPENROUTER_API_KEY           OpenRouter API key (multi-model access)
    JOB_STAR_MODEL               Override default model (default: ollama/glm-5.2)
    DATABASE_URL                 Postgres connection string
                                 (default: postgresql://jobstar:jobstar@localhost:5432/job_star)

  EXAMPLES:
    job-star add "Fix the socket timeout bug" --urgency imperative
    job-star add "Make gatehouse TUI available" --urgency idle-opportunistic --domain coding
    job-star list --status active
    job-star work abc12345
    job-star digest 50

  The loop begins. 🦞
`);
}

// ============================================================================
// HELPERS
// ============================================================================

interface ParsedArgs {
  positional: string[];
  flags: Record<string, string>;
}

function parseFlags(args: string[], defaults: Record<string, string> = {}): ParsedArgs {
  const positional: string[] = [];
  const flags: Record<string, string> = { ...defaults };

  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith('--')) {
      const key = args[i].substring(2);
      if (i + 1 < args.length && !args[i + 1].startsWith('--')) {
        flags[key] = args[i + 1];
        i++;
      } else {
        flags[key] = 'true';
      }
    } else {
      positional.push(args[i]);
    }
  }

  return { positional, flags };
}

async function resolveGoalId(id: string): Promise<string> {
  // If it's a full UUID, use it directly
  if (id.length === 36) {
    return id;
  }

  // Try as partial UUID
  const rows = await query<GoalRow>(
    `SELECT id FROM goals WHERE id::text LIKE $1`,
    [`${id}%`]
  );

  if (rows.length === 0) {
    throw new Error(`Goal not found: ${id}`);
  }

  if (rows.length > 1) {
    throw new Error(`Ambiguous goal ID: ${id} (matches ${rows.length} goals)`);
  }

  return rows[0].id;
}

function buildPlanPrompt(goal: GoalRow): { system: string; user: string } {
  return {
    system: `You are Job-Star, a system that helps build software projects through constrained, supervised AI orchestration.

You are currently in bootstrap mode — helping to build itself. Your output will be used to plan the steps needed to achieve a goal.

Be specific, practical, and focused. Break the goal into concrete, executable steps. Each step should be something an AI coding agent could reasonably complete in one session.

Output format: List each step as a numbered item with a title and optional description.`,
    user: `Goal: ${goal.title}

${goal.description || ''}

Domain: ${goal.domain}
Urgency: ${goal.urgency}

Break this goal into concrete steps. For each step, provide:
1. A clear title
2. A brief description of what needs to be done
3. Any files or components that should be created or modified

Be practical. These steps will be executed one at a time by an AI agent.`,
  };
}

interface PreviousStep {
  title: string;
  model: string | null;
  resultContent: string;
}

function buildWorkPrompt(goal: GoalRow, step: StepRow, previousSteps: PreviousStep[] = []): { system: string; user: string } {
  // Build context from previous steps — file structure and key decisions
  let prevContext = '';
  if (previousSteps.length > 0) {
    const fileList: string[] = [];
    const summaries: string[] = [];

    for (const ps of previousSteps) {
      // Extract file paths from the AI output (look for "File:" headers and code blocks)
      const content = ps.resultContent;
      const fileMatches = content.matchAll(/(?:File(?:s)?(?:\s+to\s+(?:create|modify))?[:\s]*)[`']([^`'\n]+\.(?:py|rs|ts|js|go|java|rb|sh|yaml|yml|json|toml|md|sql|html|css))[`']/gi);
      for (const m of fileMatches) {
        if (!fileList.includes(m[1])) {
          fileList.push(m[1]);
        }
      }
      // Also look for code block headers
      const codeFileMatches = content.matchAll(/##\s*File:\s*`?([^`\n]+)`?/gi);
      for (const m of codeFileMatches) {
        if (!fileList.includes(m[1])) {
          fileList.push(m[1]);
        }
      }

      // Extract the first few lines as a summary
      const lines = content.split('\n').slice(0, 5).join(' ');
      summaries.push(`  - ${ps.title} [${ps.model || '?'}]: ${lines.substring(0, 150)}...`);
    }

    prevContext = `

PREVIOUS STEPS COMPLETED (${previousSteps.length}):
${summaries.join('\n')}

FILES ALREADY CREATED (${fileList.length}):
${fileList.map(f => '  ' + f).join('\n')}

MANDATORY CONSTRAINT: You MUST use the EXACT file paths from previous steps. DO NOT create new module trees.
Do NOT create new module trees (e.g., if previous steps use 'jobstar/triage/', use that path, not 'triage/' or 'job_star/triage/').
Be consistent with naming conventions, import paths, and directory structure from previous steps.`;
  }

  return {
    system: `You are Job-Star, working on a specific step of a larger goal.

You are operating under these constraints:
- You are in bootstrap mode, helping to build the Job-Star system itself
- Be practical and specific
- Generate actual code, configs, or documentation as needed
- If you're creating a file, include the full file content
- If you're modifying a file, show the complete modified version
- Explain what you're doing and why
- Be CONSISTENT with the module structure and file paths from previous steps

This is a supervised system. Your output will be reviewed by a human before being applied.`,
    user: `Goal: ${goal.title}
${goal.description || ''}
${prevContext}

Current Step: ${step.title}
${step.description || ''}

Domain: ${goal.domain}
Urgency: ${goal.urgency}

Complete this step. Generate the code, configuration, or documentation needed.
Be thorough and specific. Include full file contents where applicable.
Use the SAME file paths and module structure as previous steps.`,
  };
}