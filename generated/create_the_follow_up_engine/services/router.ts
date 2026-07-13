/**
 * Channel Router
 * --------------
 * Receives an escalation (with classified urgency) plus the user's current
 * flow state, consults the routing matrix, applies runtime guardrails
 * (cooldowns, forced delivery on excessive deferrals), and returns a
 * RoutingDecision describing what to do next.
 *
 * The router is pure: it does not perform delivery itself. The caller
 * (notification dispatcher) acts on the returned decision.
 */

import { ROUTING_MATRIX, COOLDOWN_MS, MAX_DEFERRALS_BEFORE_FORCE, MatrixCell, Channel, QueueHint } from '../config/routing-matrix';

export type Urgency = 'INTERRUPT' | 'BATCH' | 'SILENT';
export type FlowState = 'AVAILABLE' | 'FOCUSED' | 'DEEP_WORK' | 'AWAY' | 'OFFLINE';
export type DeliveryAction = 'DELIVER_NOW' | 'QUEUE' | 'LOG_ONLY';

export interface Escalation {
  /** Unique key for cooldown / dedup tracking. */
  key: string;
  urgency: Urgency;
  /** Human-readable title for the notification. */
  title: string;
  /** Body / detail. */
  body: string;
  /** Source system that raised the escalation. */
  source: string;
  /** How many times this escalation has been deferred so far. */
  deferralCount?: number;
  /** Timestamp (ms epoch) the escalation was first raised. */
  raisedAt: number;
  /** Timestamp (ms epoch) of the most recent delivery attempt, if any. */
  lastDeliveredAt?: number;
}

export interface RoutingContext {
  flowState: FlowState;
  /** Current time, injectable for testing. Defaults to Date.now(). */
  now?: number;
}

export interface RoutingDecision {
  action: DeliveryAction;
  channel: Channel | null;
  reason: string;
  queueHint?: QueueHint;
  /** The escalation this decision applies to. */
  escalationKey: string;
  /** Whether the decision overrode the base matrix (for audit). */
  overridden: boolean;
  /** Original matrix action before guardrails, for telemetry. */
  baseAction: DeliveryAction;
  timestamp: number;
}

/**
 * Route an escalation to a delivery decision.
 */
export function route(
  escalation: Escalation,
  context: RoutingContext
): RoutingDecision {
  const now = context.now ?? Date.now();
  const cell: MatrixCell = ROUTING_MATRIX[escalation.urgency][context.flowState];
  const baseAction = cell.action;

  // Guardrail 1: SILENT always logs — no overrides.
  if (escalation.urgency === 'SILENT') {
    return buildDecision(escalation, cell, baseAction, false, now);
  }

  // Guardrail 2: Forced delivery on excessive deferrals when user is available.
  // If something has been deferred too many times and the user is now
  // AVAILABLE, promote QUEUE → DELIVER_NOW regardless of urgency.
  const deferrals = escalation.deferralCount ?? 0;
  if (
    deferrals >= MAX_DEFERRALS_BEFORE_FORCE &&
    context.flowState === 'AVAILABLE' &&
    baseAction === 'QUEUE'
  ) {
    return buildDecision(
      escalation,
      {
        ...cell,
        action: 'DELIVER_NOW',
        channel: 'in_app_banner',
        reason: `Escalation deferred ${deferrals} times — forcing delivery on availability.`,
      },
      baseAction,
      true,
      now
    );
  }

  // Guardrail 3: Cooldown suppression for DELIVER_NOW.
  // If the same escalation was delivered recently, degrade to QUEUE
  // to avoid nagging the user.
  if (baseAction === 'DELIVER_NOW' && escalation.lastDeliveredAt !== undefined) {
    const cooldown = COOLDOWN_MS[escalation.urgency];
    const elapsed = now - escalation.lastDeliveredAt;
    if (elapsed < cooldown) {
      return buildDecision(
        escalation,
        {
          ...cell,
          action: 'QUEUE',
          reason: `Delivery suppressed by cooldown (${Math.round(elapsed / 1000)}s since last delivery; cooldown ${cooldown / 1000}s). Queued for retry.`,
          queueHint: 'immediate_retry',
        },
        baseAction,
        true,
        now
      );
    }
  }

  return buildDecision(escalation, cell, baseAction, false, now);
}

function buildDecision(
  escalation: Escalation,
  cell: MatrixCell,
  baseAction: DeliveryAction,
  overridden: boolean,
  now: number
): RoutingDecision {
  return {
    action: cell.action,
    channel: cell.channel,
    reason: cell.reason,
    queueHint: cell.queueHint,
    escalationKey: escalation.key,
    overridden,
    baseAction,
    timestamp: now,
  };
}

/**
 * Convenience: route many escalations at once against the same context.
 * Useful for the batch dispatcher that flushes the queue on a break.
 */
export function routeBatch(
  escalations: Escalation[],
  context: RoutingContext
): RoutingDecision[] {
  return escalations.map((e) => route(e, context));
}

/**
 * Helper for the queue manager: given a queued escalation and a new
 * flow state, decide whether it's time to flush it.
 * Returns true if the action would be DELIVER_NOW.
 */
export function shouldFlush(
  escalation: Escalation,
  context: RoutingContext
): boolean {
  return route(escalation, context).action === 'DELIVER_NOW';
}
