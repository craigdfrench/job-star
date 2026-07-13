/**
 * Job-Star: Database connection
 *
 * The database IS the goal registry. It's the shared brain.
 * Every component talks to Postgres. This is the single source of truth.
 */

import pg from 'pg';

const { Pool } = pg;

export interface GoalRow {
  id: string;
  parent_id: string | null;
  title: string;
  description: string | null;
  domain: string;
  status: string;
  urgency: string;
  progress: number;
  blockers: string[];
  deadline: string | null;
  source: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface StepRow {
  id: string;
  goal_id: string;
  title: string;
  description: string | null;
  status: string;
  order_index: number;
  result: Record<string, unknown> | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost: number;
  attempted_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AuditRow {
  id: string;
  goal_id: string | null;
  step_id: string | null;
  event: string;
  details: Record<string, unknown>;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost: number;
  timestamp: string;
}

let pool: pg.Pool | null = null;

export function getPool(): pg.Pool {
  if (!pool) {
    const connectionString = process.env.DATABASE_URL ||
      'postgresql://jobstar:jobstar@localhost:5432/job_star';

    pool = new Pool({
      connectionString,
      max: 10,
      idleTimeoutMillis: 30000,
      connectionTimeoutMillis: 5000,
    });

    pool.on('error', (err) => {
      console.error('Unexpected error on idle database client', err);
    });
  }
  return pool;
}

export async function query<T extends pg.QueryResultRow = pg.QueryResultRow>(
  text: string,
  params?: unknown[]
): Promise<T[]> {
  const client = await getPool().connect();
  try {
    const result = await client.query<T>(text, params as unknown[]);
    return result.rows;
  } finally {
    client.release();
  }
}

export async function queryOne<T extends pg.QueryResultRow = pg.QueryResultRow>(
  text: string,
  params?: unknown[]
): Promise<T | null> {
  const rows = await query<T>(text, params);
  return rows[0] || null;
}

export async function closePool(): Promise<void> {
  if (pool) {
    await pool.end();
    pool = null;
  }
}

// ============================================================================
// AUDIT HELPER — every significant action gets logged
// ============================================================================

export async function audit(
  event: string,
  details: Record<string, unknown> = {},
  goalId?: string,
  stepId?: string,
  model?: string,
  cost = 0
): Promise<void> {
  await query(
    `INSERT INTO audit_trail (goal_id, step_id, event, details, model, cost)
     VALUES ($1, $2, $3, $4, $5, $6)`,
    [goalId || null, stepId || null, event, JSON.stringify(details), model || null, cost]
  );
}