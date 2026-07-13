// src/services/notification-service.ts
//
// Job-Star Notification Service — the orchestrator.
//
// Pipeline: receiveEscalation(event)
//   1. classify urgency          (src/services/classifier.ts)
//   2. check current flow state  (src/services/flow-state.ts)
//   3. route to channel          (src/services/channel-router.ts)
//   4. deliver immediately OR queue for batch
//
// flushBatch() drains the batch queue and delivers accumulated messages
// through the batch channel.

import { classifyUrgency } from './classifier';
import { getFlowState, type FlowState } from './flow-state';
import { routeToChannel, type RoutingDecision, type ChannelName } from './channel-router';
import { deliverInterrupt } from './channels/interrupt-delivery';
import { deliverBatch } from './channels/batch-delivery';
import { deliverSilent } from './channels/silent-delivery';
import type { EscalationEvent } from '../config/urgency-rules';
import type { UrgencyClassification } from '../config/urgency-rules';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** A snapshot of one escalation as it moved through the pipeline. */
export interface EventLogEntry {
  id: string;
  receivedAt: number;
  event: EscalationEvent;
  classification: UrgencyClassification;
  flowState: FlowState;
  routing: RoutingDecision;
  outcome: 'delivered' | 'queued' | 'silenced' | 'dropped';
  deliveredAt?: number;
  channel?: ChannelName;
  error?: string;
}

/** Options for receiveEscalation. */
export interface ReceiveOptions {
  /** Override the flow-state check (useful for tests / forced delivery). */
  skipFlowCheck?: boolean;
  /** Force a specific flow state instead of reading the tracker. */
  forceFlowState?: FlowState;
}

// ---------------------------------------------------------------------------
// Internal state
// ---------------------------------------------------------------------------

const eventLog: EventLogEntry[] = [];
const batchQueue: EventLogEntry[] = [];

let batchFlushTimer: ReturnType<typeof setTimeout> | null = null;
const DEFAULT_BATCH_FLUSH_MS = 5 * 60 * 1000; // 5 minutes

// Simple id generator — good enough for an in-memory bootstrap service.
let idCounter = 0;
function nextId(): string {
  idCounter += 1;
  return `esc-${Date.now().toString(36)}-${idCounter.toString(36)}`;
}

// ---------------------------------------------------------------------------
// Delivery dispatch
// ---------------------------------------------------------------------------

async function dispatch(
  channel: ChannelName,
  entry: EventLogEntry
): Promise<void> {
  switch (channel) {
    case 'interrupt':
      await deliverInterrupt(entry.event, entry.classification, entry.routing);
      break;
    case 'batch':
      await deliverBatch([entry.event], entry.classification, entry.routing);
      break;
    case 'silent':
      await deliverSilent(entry.event, entry.classification, entry.routing);
      break;
    default: {
      const _exhaustive: never = channel;
      throw new Error(`Unknown channel: ${_exhaustive as string}`);
    }
  }
}

// ---------------------------------------------------------------------------
// Batch scheduling
// ---------------------------------------------------------------------------

function scheduleBatchFlush(timeoutMs: number = DEFAULT_BATCH_FLUSH_MS): void {
  if (batchFlushTimer) return; // already scheduled
  batchFlushTimer = setTimeout(() => {
    batchFlushTimer = null;
    void flushBatch();
  }, timeoutMs);
}

function cancelBatchFlush(): void {
  if (batchFlushTimer) {
    clearTimeout(batchFlushTimer);
    batchFlushTimer = null;
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Receive an escalation event and run it through the full pipeline.
 *
 * Returns the log entry describing what happened. Callers can inspect
 * `outcome` and `channel` to know whether the user was interrupted,
 * whether the message was queued for batch, or silenced.
 */
export async function receiveEscalation(
  event: EscalationEvent,
  options: ReceiveOptions = {}
): Promise<EventLogEntry> {
  const id = nextId();
  const receivedAt = Date.now();

  // 1. Classify urgency.
  const classification = classifyUrgency(event);

  // 2. Determine flow state.
  const flowState: FlowState = options.forceFlowState
    ?? (options.skipFlowCheck ? 'available' : getFlowState());

  // 3. Route.
  const routing = routeToChannel(classification, flowState);

  // 4. Build the log entry (mutated as we go).
  const entry: EventLogEntry = {
    id,
    receivedAt,
    event,
    classification,
    flowState,
    routing,
    outcome: 'dropped', // overwritten below
  };

  try {
    if (routing.channel === 'interrupt') {
      // Deliver immediately — this is the "interrupt the user" path.
      await dispatch('interrupt', entry);
      entry.outcome = 'delivered';
      entry.channel = 'interrupt';
      entry.deliveredAt = Date.now();
    } else if (routing.channel === 'batch') {
      // Queue for later flush.
      batchQueue.push(entry);
      scheduleBatchFlush(routing.batchTimeoutMs ?? DEFAULT_BATCH_FLUSH_MS);
      entry.outcome = 'queued';
      entry.channel = 'batch';
    } else {
      // Silent channel — record but don't push to user.
      await dispatch('silent', entry);
      entry.outcome = 'silenced';
      entry.channel = 'silent';
      entry.deliveredAt = Date.now();
    }
  } catch (err) {
    entry.outcome = 'dropped';
    entry.error = err instanceof Error ? err.message : String(err);
  }

  eventLog.push(entry);
  return entry;
}

/**
 * Drain the batch queue and deliver all accumulated messages in one
 * consolidated batch notification. Returns the entries that were flushed.
 *
 * If the queue is empty, this is a no-op.
 */
export async function flushBatch(): Promise<EventLogEntry[]> {
  if (batchQueue.length === 0) return [];
  cancelBatchFlush();

  const toFlush = batchQueue.splice(0, batchQueue.length);

  // Group by classification so the batch channel can present coherent sections.
  // For now we deliver as a single batch using the first entry's routing context.
  const head = toFlush[0];
  const events = toFlush.map((e) => e.event);

  try {
    await deliverBatch(events, head.classification, head.routing);
    for (const e of toFlush) {
      e.outcome = 'delivered';
      e.deliveredAt = Date.now();
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    for (const e of toFlush) {
      e.outcome = 'dropped';
      e.error = msg;
    }
  }

  return toFlush;
}

// ---------------------------------------------------------------------------
// Introspection helpers (used by other Job-Star components and tests)
// ---------------------------------------------------------------------------

/** Return a copy of the full event log. */
export function getEventLog(): EventLogEntry[] {
  return [...eventLog];
}

/** Return entries matching a predicate. */
export function queryLog(pred: (e: EventLogEntry) => boolean): EventLogEntry[] {
  return eventLog.filter(pred);
}

/** Current batch queue depth. */
export function batchQueueDepth(): number {
  return batchQueue.length;
}

/** Clear all in-memory state. Intended for tests. */
export function _resetForTest(): void {
  eventLog.length = 0;
  batchQueue.length = 0;
  cancelBatchFlush();
  idCounter = 0;
}
