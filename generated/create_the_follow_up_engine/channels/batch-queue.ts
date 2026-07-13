// src/channels/batch-queue.ts
// Accumulates escalations and flushes them as a digest.

import type { NotificationChannel, Escalation, DeliveryResult } from './base';
import { formatEscalation } from './base';

/**
 * BatchQueue collects escalations and emits them as a single digest
 * when `flush()` is called — either by a periodic timer or an explicit
 * trigger (e.g. user returning from flow state). Intended for `batch`
 * urgency.
 */
export class BatchQueue implements NotificationChannel {
  readonly name = 'batch';

  private readonly queue: Escalation[] = [];
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(
    /**
     * Sink invoked with the formatted digest on flush.
     * Defaults to stdout so it composes with TerminalChannel's writer.
     */
    private readonly sink: (digest: string) => void = (digest) => process.stdout.write(digest + '\n'),
    /** Auto-flush interval in ms. 0 disables auto-flush. */
    private readonly flushIntervalMs: number = 0,
  ) {
    if (flushIntervalMs > 0) {
      this.timer = setInterval(() => this.flush(), flushIntervalMs);
      // Don't keep the event loop alive solely for batch flushing.
      if (this.timer && typeof this.timer.unref === 'function') this.timer.unref();
    }
  }

  deliver(e: Escalation): DeliveryResult {
    this.queue.push(e);
    return { channel: this.name, delivered: false, queued: true, at: Date.now() };
  }

  get size(): number {
    return this.queue.length;
  }

  /**
   * Flush all queued escalations as a single digest. Returns the
   * number of items flushed.
   */
  flush(): number {
    if (this.queue.length === 0) return 0;
    const items = this.queue.splice(0);
    const header = `── Job-Star digest (${items.length} item${items.length === 1 ? '' : 's'}) ──`;
    const body = items.map(formatEscalation).join('\n');
    const footer = `── end digest ──`;
    this.sink(`${header}\n${body}\n${footer}`);
    return items.length;
  }

  /** Stop any auto-flush timer. Safe to call multiple times. */
  dispose(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.flush();
  }
}
