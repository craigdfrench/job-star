/**
 * Urgency classification for escalations.
 *
 * The follow-up engine classifies every incoming escalation into one of
 * three urgency tiers. The tier determines *how* (and whether) the user
 * is surfaced the information, and is influenced by both the source
 * component's suggestion and the user's current flow state.
 */

export enum UrgencyLevel {
  /**
   * Break the user's current flow and surface immediately.
   *
   * Reserved for time-critical, high-confidence signals where inaction
   * has a real cost: a deployment failing, a deadline slipping in the
   * next hour, a blocked teammate waiting on a decision.
   *
   * Delivery: toast / push / interrupt banner.
   * Respect for flow state: overrides "focus" mode but not "do-not-disturb"
   * unless the escalation is also flagged critical.
   */
  INTERRUPT = 'INTERRUPT',

  /**
   * Collect into the next batch digest.
   *
   * The default tier for most escalations. The user will see this when
   * they next check in, when a natural break is detected, or when the
   * batch timer fires — whichever comes first.
   *
   * Delivery: batch digest, inbox-style queue.
   * Respect for flow state: fully respects focus mode; deferred until break.
   */
  BATCH = 'BATCH',

  /**
   * Log only; do not proactively surface.
   *
   * For low-signal or ambient information: "job X completed successfully",
   * "cache warmed", "metrics within normal range". Available on query or
   * in a silent log, but never pushed to the user.
   *
   * Delivery: written to the escalation log; visible in review surfaces only.
   * Respect for flow state: always silent regardless of flow state.
   */
  SILENT = 'SILENT',
}

/**
 * Lifecycle of an escalation from intake to resolution.
 *
 * These statuses are append-only in spirit — an escalation moves forward
 * through the pipeline. The one exception is SUPPRESSED → PENDING, which
 * can happen when a suppressed escalation is re-evaluated after a flow
 * state change (e.g. user exits focus mode and a deferred INTERRUPT
 * becomes eligible again).
 */
export enum DeliveryStatus {
  /** Just received by the engine; not yet classified. */
  PENDING = 'PENDING',

  /** Urgency has been assigned (may differ from suggestedUrgency). Awaiting delivery slot. */
  CLASSIFIED = 'CLASSIFIED',

  /** Intentionally held back due to flow state or rate-limiting. Will be reconsidered. */
  SUPPRESSED = 'SUPPRESSED',

  /** Surfaced to the user through the appropriate channel. */
  DELIVERED = 'DELIVERED',

  /** User has seen/interacted with the notification (ack, click, dismiss). */
  ACKNOWLEDGED = 'ACKNOWLEDGED',

  /** The underlying situation is resolved or the escalation is no longer relevant. */
  RESOLVED = 'RESOLVED',

  /** Stale — the window for usefulness passed before delivery. Kept for audit. */
  EXPIRED = 'EXPIRED',
}

/**
 * Whether an INTERRUPT-tier escalation may break through do-not-disturb.
 * Set by the classifier based on source component and content, not by
 * the originating component directly (to prevent alert fatigue).
 */
export type Criticality = 'normal' | 'critical';

/**
 * A snapshot of the user's flow state at the moment of classification.
 * Captured so the dispatcher and audit log can reason about *why* an
 * escalation was delivered or suppressed.
 */
export interface FlowStateSnapshot {
  /** "focus" | "available" | "away" | "dnd" */
  mode: string;
  /** Free-text label of what the user was doing, if known. */
  activity?: string;
  /** Epoch ms when this flow state began. */
  since: number;
}

/**
 * Helper ordering for urgency — higher number = more urgent.
 * Useful for comparisons and for "promote the max" merge logic.
 */
export const URGENCY_RANK: Record<UrgencyLevel, number> = {
  [UrgencyLevel.SILENT]: 0,
  [UrgencyLevel.BATCH]: 1,
  [UrgencyLevel.INTERRUPT]: 2,
};

/** Returns the more urgent of two levels. */
export function maxUrgency(a: UrgencyLevel, b: UrgencyLevel): UrgencyLevel {
  return URGENCY_RANK[a] >= URGENCY_RANK[b] ? a : b;
}
