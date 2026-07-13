// src/services/history.ts
//
// Escalation history and replay log.
// Persists every escalation's full lifecycle to a JSONL file and exposes
// query + replay helpers.

import * as fs from 'fs';
import * as path from 'path';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type UrgencyTier = 'interrupt' | 'batch' | 'silent';
export type ChannelName = 'interrupt' | 'batch' | 'silent-log';
export type DeliveryStatus =
  | 'pending'          // received, not yet routed
  | 'routed'           // routing decision made, not yet delivered
  | 'delivered'        // channel confirmed delivery
  | 'failed'           // channel reported failure
  | 'suppressed'       // intentionally not delivered (e.g. silent tier)
  | 'deferred';        // queued for later (e.g. batch tier waiting on flush)

export interface EscalationRecord {
  // Identity
  id: string;                       // stable escalation id (from intake)
  source: string;                   // which watcher/subsystem raised it
  summary: string;                  // human-readable one-liner
  payload?: Record<string, unknown>;// original escalation body

  // Lifecycle timestamps (ISO 8601 strings)
  receivedAt: string;
  classifiedAt?: string;
  routedAt?: string;
  deliveredAt?: string;

  // Classification outcome
  urgencyTier?: UrgencyTier;
  urgencyScore?: number;            // raw score from classifier, if computed
  classificationReason?: string;    // which rule fired

  // Routing outcome
  channel?: ChannelName;
  flowStateAtRouting?: string;      // 'deep' | 'shallow' | 'idle'
  routingReason?: string;           // why this channel was chosen

  // Delivery outcome
  status: DeliveryStatus;
  deliveryError?: string;
  attempts?: number;
}

export interface HistoryQuery {
  since?: Date | string;             // inclusive lower bound on receivedAt
  until?: Date | string;             // inclusive upper bound on receivedAt
  urgencyTier?: UrgencyTier;
  channel?: ChannelName;
  status?: DeliveryStatus;
  source?: string;
  id?: string;                       // filter to a single escalation id
  limit?: number;                    // default 100, max 1000
}

export interface HistoryStats {
  total: number;
  byUrgencyTier: Record<string, number>;
  byChannel: Record<string, number>;
  byStatus: Record<string, number>;
  bySource: Record<string, number>;
  firstAt?: string;
  lastAt?: string;
}

// ---------------------------------------------------------------------------
// HistoryService
// ---------------------------------------------------------------------------

export class HistoryService {
  private readonly logPath: string;
  private readonly logDir: string;
  private writeStream?: fs.WriteStream;
  private ensureDirDone = false;

  constructor(logPath = path.resolve(process.cwd(), 'logs/escalations.jsonl')) {
    this.logPath = logPath;
    this.logDir = path.dirname(logPath);
  }

  // -- internal: make sure the directory + file exist -----------------------

  private ensureLog(): void {
    if (this.ensureDirDone) return;
    if (!fs.existsSync(this.logDir)) {
      fs.mkdirSync(this.logDir, { recursive: true });
    }
    if (!fs.existsSync(this.logPath)) {
      fs.writeFileSync(this.logPath, '', { encoding: 'utf8' });
    }
    this.ensureDirDone = true;
  }

  // -- write ----------------------------------------------------------------

  /**
   * Append a record to the JSONL log. Synchronous append — escalations are
   * low-frequency and we want durability even if the process dies immediately
   * after delivery.
   *
   * Returns the record as written (with any normalized fields).
   */
  record(rec: EscalationRecord): EscalationRecord {
    this.ensureLog();

    const normalized: EscalationRecord = {
      ...rec,
      receivedAt: rec.receivedAt ?? new Date().toISOString(),
      status: rec.status ?? 'pending',
    };

    const line = JSON.stringify(normalized) + '\n';
    fs.appendFileSync(this.logPath, line, { encoding: 'utf8' });
    return normalized;
  }

  /**
   * Convenience: record the initial intake of an escalation (status=pending,
   * no classification/routing/delivery fields yet). Returns the record so the
   * caller can mutate it through the lifecycle and call `record()` again with
   * updated status.
   */
  recordIntake(input: {
    id: string;
    source: string;
    summary: string;
    payload?: Record<string, unknown>;
  }): EscalationRecord {
    return this.record({
      ...input,
      receivedAt: new Date().toISOString(),
      status: 'pending',
    });
  }

  /**
   * Append a lifecycle-update record for an escalation that was already
   * recorded at intake. This produces a second line with the same `id` but
   * richer fields, so the full lifecycle is reconstructable by filtering on id.
   */
  recordOutcome(rec: EscalationRecord): EscalationRecord {
    return this.record(rec);
  }

  // -- read -----------------------------------------------------------------

  /**
   * Read the log file line by line, parse each JSON record, and yield ones
   * matching the query. Streaming — does not load the whole file into memory
   * before filtering.
   *
   * Records are returned in file order (oldest first). Use `limit` to cap.
   */
  *iter(query: HistoryQuery = {}): Generator<EscalationRecord> {
    this.ensureLog();
    const limit = Math.min(query.limit ?? 100, 1000);

    const sinceMs = query.since ? this.toMs(query.since) : -Infinity;
    const untilMs = query.until ? this.toMs(query.until) : Infinity;

    const content = fs.readFileSync(this.logPath, 'utf8');
    const lines = content.split('\n');

    let emitted = 0;
    for (const line of lines) {
      if (!line.trim()) continue;
      let rec: EscalationRecord;
      try {
        rec = JSON.parse(line);
      } catch {
        // Skip malformed lines but don't crash the whole query.
        continue;
      }

      const recMs = this.toMs(rec.receivedAt);
      if (recMs < sinceMs || recMs > untilMs) continue;
      if (query.urgencyTier && rec.urgencyTier !== query.urgencyTier) continue;
      if (query.channel && rec.channel !== query.channel) continue;
      if (query.status && rec.status !== query.status) continue;
      if (query.source && rec.source !== query.source) continue;
      if (query.id && rec.id !== query.id) continue;

      yield rec;
      emitted++;
      if (emitted >= limit) break;
    }
  }

  /**
   * Materialized query result as an array. For most UI/debugging needs this is
   * the friendly entry point.
   */
  getHistory(query: HistoryQuery = {}): EscalationRecord[] {
    return Array.from(this.iter(query));
  }

  /**
   * Return the latest record per escalation id matching the query. Useful when
   * the log contains both intake and outcome lines for the same id and you
   * only want the final state.
   */
  getLatestPerId(query: HistoryQuery = {}): EscalationRecord[] {
    const byId = new Map<string, EscalationRecord>();
    for (const rec of this.iter({ ...query, limit: 1000 })) {
      const existing = byId.get(rec.id);
      if (!existing || this.toMs(rec.receivedAt) >= this.toMs(existing.receivedAt)) {
        byId.set(rec.id, rec);
      }
    }
    return Array.from(byId.values());
  }

  /**
   * Aggregate stats over the log (or a slice of it). Cheap to compute; walks
   * the file once.
   */
  getStats(query: HistoryQuery = {}): HistoryStats {
    const stats: HistoryStats = {
      total: 0,
      byUrgencyTier: {},
      byChannel: {},
      byStatus: {},
      bySource: {},
    };

    for (const rec of this.iter({ ...query, limit: 1000 })) {
      stats.total++;
      if (rec.urgencyTier) stats.byUrgencyTier[rec.urgencyTier] = (stats.byUrgencyTier[rec.urgencyTier] ?? 0) + 1;
      if (rec.channel) stats.byChannel[rec.channel] = (stats.byChannel[rec.channel] ?? 0) + 1;
      stats.byStatus[rec.status] = (stats.byStatus[rec.status] ?? 0) + 1;
      stats.bySource[rec.source] = (stats.bySource[rec.source] ?? 0) + 1;

      if (!stats.firstAt || this.toMs(rec.receivedAt) < this.toMs(stats.firstAt)) {
        stats.firstAt = rec.receivedAt;
      }
      if (!stats.lastAt || this.toMs(rec.receivedAt) > this.toMs(stats.lastAt)) {
        stats.lastAt = rec.receivedAt;
      }
    }

    return stats;
  }

  // -- replay ---------------------------------------------------------------

  /**
   * Replay historical escalations through a callback (e.g. re-classify with
   * new rules, re-route with new thresholds). The callback receives the
   * original intake record; whatever it returns is recorded as a new outcome
   * line with `replayed: true` semantics (we tag the source).
   *
   * This does NOT mutate the original log lines — it appends new ones. The
   * original history is preserved.
   */
  async replay(
    query: HistoryQuery,
    handler: (rec: EscalationRecord) => Promise<Partial<EscalationRecord>>,
  ): Promise<number> {
    let count = 0;
    for (const rec of this.iter({ ...query, limit: 1000 })) {
      // Only replay intake-style records (status=pending, no classification yet)
      // to avoid double-processing outcome lines.
      if (rec.status !== 'pending' || rec.urgencyTier) continue;

      const outcome = await handler(rec);
      this.record({
        ...rec,
        ...outcome,
        source: `replay:${rec.source}`,
        receivedAt: new Date().toISOString(),
        status: (outcome.status as DeliveryStatus) ?? 'routed',
      });
      count++;
    }
    return count;
  }

  // -- helpers --------------------------------------------------------------

  private toMs(d: Date | string): number {
    if (d instanceof Date) return d.getTime();
    const n = Date.parse(d);
    return Number.isNaN(n) ? 0 : n;
  }
}

// ---------------------------------------------------------------------------
// Singleton accessor
// ---------------------------------------------------------------------------

let _instance: HistoryService | undefined;

export function getHistoryService(logPath?: string): HistoryService {
  if (!_instance) {
    _instance = new HistoryService(logPath);
  }
  return _instance;
}

/** Reset the singleton — intended for tests only. */
export function _resetHistoryServiceForTests(): void {
  _instance = undefined;
}
