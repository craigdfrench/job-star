// src/channels/batch-formatter.ts
//
// Batch Formatter
// ---------------
// Groups a collection of batched notifications by their source and produces
// a structured + human-readable "batch ready" summary.
//
// The structured form is returned so the delivery channel (email digest,
// Slack summary, in-app tray) can render it however it likes. The text
// form is a fallback for channels that only accept strings.

export interface BatchedNotification {
  id: string;
  source: string;          // e.g. "application", "interview", "deadline"
  title: string;
  body: string;
  urgency: 'interrupt' | 'batch' | 'silent';
  receivedAt: number;      // epoch ms
  meta?: Record<string, unknown>;
}

export interface BatchSummaryGroup {
  source: string;
  count: number;
  items: BatchedNotification[];
}

export interface BatchSummary {
  generatedAt: number;
  totalItems: number;
  groupCount: number;
  groups: BatchSummaryGroup[];
  text: string;
}

/**
 * Group notifications by source, preserving insertion order within each group.
 */
export function groupBySource(
  notifications: BatchedNotification[]
): BatchSummaryGroup[] {
  const order: string[] = [];
  const map = new Map<string, BatchedNotification[]>();

  for (const n of notifications) {
    if (!map.has(n.source)) {
      map.set(n.source, []);
      order.push(n.source);
    }
    map.get(n.source)!.push(n);
  }

  return order.map((source) => ({
    source,
    count: map.get(source)!.length,
    items: map.get(source)!,
  }));
}

/**
 * Render a human-readable summary string from grouped notifications.
 *
 * Example output:
 *
 *   📬 4 updates ready (3 sources)
 *
 *   • application (2)
 *     - "Acme Corp" responded to your application
 *     - "Globex" viewed your profile
 *   • interview (1)
 *     - Interview scheduled with Initech for Thursday
 *   • deadline (1)
 *     - "Hooli" application closes in 2 days
 */
export function renderSummaryText(groups: BatchSummaryGroup[]): string {
  const total = groups.reduce((sum, g) => sum + g.count, 0);
  const lines: string[] = [];

  lines.push(`📬 ${total} update${total === 1 ? '' : 's'} ready (${groups.length} source${groups.length === 1 ? '' : 's'})`);
  lines.push('');

  for (const group of groups) {
    lines.push(`• ${group.source} (${group.count})`);
    for (const item of group.items) {
      lines.push(`  - ${item.title}`);
    }
  }

  return lines.join('\n');
}

/**
 * Build a full BatchSummary from a flat list of batched notifications.
 */
export function formatBatch(
  notifications: BatchedNotification[]
): BatchSummary {
  const groups = groupBySource(notifications);
  return {
    generatedAt: Date.now(),
    totalItems: notifications.length,
    groupCount: groups.length,
    groups,
    text: renderSummaryText(groups),
  };
}


// --- DUPLICATE BLOCK ---

// src/services/batch-scheduler.ts
//
// Batch Flush Scheduler
// ---------------------
// Decides WHEN batched notifications get flushed (delivered).
//
// Three triggers:
//   1. Cadence       — a fixed interval (e.g. every 30 minutes) while running.
//   2. Max-age       — if the oldest batched item exceeds maxBatchAgeMs, flush
//                      immediately so nothing rots in the queue.
//   3. Availability  — when the user's flow state transitions to "available",
//                      we flush opportunistically (idle-opportunistic domain).
//
// The scheduler is deliberately decoupled from storage: it calls a provided
// `collectBatch()` function to read pending items and `deliver()` to send the
// formatted summary. This lets the orchestrator own the actual queue.

import { formatBatch, BatchSummary, BatchedNotification } from '../channels/batch-formatter';

export type FlowState = 'deep' | 'shallow' | 'available';

export interface BatchSchedulerConfig {
  /** Fixed cadence flush interval in ms. 0 disables cadence flushing. */
  cadenceMs: number;
  /** Max age of the oldest batched item before a forced flush. 0 disables. */
  maxBatchAgeMs: number;
  /** Whether to flush when the user becomes available. */
  flushOnAvailable: boolean;
  /** Minimum items required before a cadence flush triggers. */
  minItemsForCadenceFlush: number;
}

export const DEFAULT_BATCH_SCHEDULER_CONFIG: BatchSchedulerConfig = {
  cadenceMs: 30 * 60 * 1000,        // 30 minutes
  maxBatchAgeMs: 4 * 60 * 60 * 1000, // 4 hours
  flushOnAvailable: true,
  minItemsForCadenceFlush: 1,
};

export interface BatchSchedulerDeps {
  /** Returns currently batched (pending) notifications. */
  collectBatch: () => BatchedNotification[];
  /** Removes items from the batch queue after they've been delivered. */
  clearBatch: (ids: string[]) => void;
  /** Delivers the formatted summary through the batch channel. */
  deliver: (summary: BatchSummary) => Promise<void>;
  /** Optional: observe flow-state changes. Provided by FlowStateTracker. */
  onFlowStateChange?: (handler: (state: FlowState) => void) => () => void;
}

export class BatchScheduler {
  private config: BatchSchedulerConfig;
  private deps: BatchSchedulerDeps;
  private cadenceTimer: ReturnType<typeof setInterval> | null = null;
  private ageTimer: ReturnType<typeof setInterval> | null = null;
  private unsubscribeFlow: (() => void) | null = null;
  private lastFlowState: FlowState | null = null;
  private running = false;

  constructor(
    deps: BatchSchedulerDeps,
    config: Partial<BatchSchedulerConfig> = {}
  ) {
    this.deps = deps;
    this.config = { ...DEFAULT_BATCH_SCHEDULER_CONFIG, ...config };
  }

  /**
   * Start the scheduler. Begins cadence + age timers and (optionally)
   * subscribes to flow-state changes.
   */
  start(): void {
    if (this.running) return;
    this.running = true;

    if (this.config.cadenceMs > 0) {
      this.cadenceTimer = setInterval(
        () => this.cadenceFlush(),
        this.config.cadenceMs
      );
    }

    if (this.config.maxBatchAgeMs > 0) {
      // Check age frequently; cheap operation.
      this.ageTimer = setInterval(
        () => this.ageFlush(),
        60 * 1000 // check every minute
      );
    }

    if (this.config.flushOnAvailable && this.deps.onFlowStateChange) {
      this.unsubscribeFlow = this.deps.onFlowStateChange((state) => {
        this.handleFlowStateChange(state);
      });
    }
  }

  /**
   * Stop the scheduler and clean up all timers/subscriptions.
   */
  stop(): void {
    if (this.cadenceTimer) {
      clearInterval(this.cadenceTimer);
      this.cadenceTimer = null;
    }
    if (this.ageTimer) {
      clearInterval(this.ageTimer);
      this.ageTimer = null;
    }
    if (this.unsubscribeFlow) {
      this.unsubscribeFlow();
      this.unsubscribeFlow = null;
    }
    this.running = false;
  }

  /**
   * Force a flush right now, regardless of triggers. Returns the summary
   * that was delivered, or null if there was nothing to flush.
   */
  async flushNow(): Promise<BatchSummary | null> {
    return this.doFlush();
  }

  // ---- internal ----------------------------------------------------------

  private handleFlowStateChange(state: FlowState): void {
    const previous = this.lastFlowState;
    this.lastFlowState = state;

    // Only flush on the *transition* into available, not while already there.
    if (state === 'available' && previous !== 'available') {
      this.availabilityFlush();
    }
  }

  private async cadenceFlush(): Promise<void> {
    const batch = this.deps.collectBatch();
    if (batch.length < this.config.minItemsForCadenceFlush) return;
    await this.doFlush();
  }

  private async ageFlush(): Promise<void> {
    const batch = this.deps.collectBatch();
    if (batch.length === 0) return;
    const oldest = Math.min(...batch.map((n) => n.receivedAt));
    if (Date.now() - oldest >= this.config.maxBatchAgeMs) {
      await this.doFlush();
    }
  }

  private async availabilityFlush(): Promise<void> {
    const batch = this.deps.collectBatch();
    if (batch.length === 0) return;
    await this.doFlush();
  }

  private async doFlush(): Promise<BatchSummary | null> {
    const batch = this.deps.collectBatch();
    if (batch.length === 0) return null;

    const summary = formatBatch(batch);
    const ids = batch.map((n) => n.id);

    try {
      await this.deps.deliver(summary);
      this.deps.clearBatch(ids);
    } catch (err) {
      // Delivery failed — leave items in the queue for the next attempt.
      // The orchestrator's error handling will surface persistent failures.
      console.error('[batch-scheduler] delivery failed, retaining batch:', err);
    }

    return summary;
  }

  /** Exposed for tests / inspection. */
  isRunning(): boolean {
    return this.running;
  }

  getConfig(): BatchSchedulerConfig {
    return { ...this.config };
  }

  updateConfig(patch: Partial<BatchSchedulerConfig>): void {
    this.config = { ...this.config, ...patch };
    // Restart timers if cadence changed while running.
    if (this.running) {
      this.stop();
      this.start();
    }
  }
}
