// src/channels/base.ts
// Common interface for all notification delivery channels in Job-Star.

/**
 * Escalation event shape — mirrors the schema defined in
 * src/config/urgency-rules.ts (step 1). Kept here as a local structural
 * type so channels don't need to import the full config tree.
 */
export interface Escalation {
  id: string;
  source: string;              // which subsystem raised it
  urgency: 'interrupt' | 'batch' | 'silent';
  title: string;
  message: string;
  context?: Record<string, unknown>;
  createdAt: number;           // epoch ms
  // Optional routing hint set by the Channel Router.
  routedChannel?: 'terminal' | 'batch' | 'silent';
}

/**
 * Result returned by every channel after a delivery attempt.
 * Lets the router / supervisor log outcomes without coupling to
 * channel-specific return types.
 */
export interface DeliveryResult {
  channel: string;
  delivered: boolean;
  queued?: boolean;            // true for BatchQueue pre-flush
  error?: string;
  at: number;
}

/**
 * Common interface every channel implements.
 */
export interface NotificationChannel {
  readonly name: string;
  deliver(escalation: Escalation): Promise<DeliveryResult> | DeliveryResult;
}

/**
 * Shared helper: format an escalation into a single human-readable line.
 * Channels may override formatting, but this gives a consistent default.
 */
export function formatEscalation(e: Escalation): string {
  const ts = new Date(e.createdAt).toISOString();
  const ctx = e.context ? ` ${JSON.stringify(e.context)}` : '';
  return `[${ts}] (${e.urgency}) ${e.source}: ${e.title} — ${e.message}${ctx}`;
}
