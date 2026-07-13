/**
 * Routing Matrix
 * --------------
 * Static configuration mapping (urgency × flowState) → delivery action.
 * This is the single source of truth for the core decision table.
 * The router service applies this matrix plus runtime guardrails.
 */

import type { Urgency, FlowState, DeliveryAction } from '../services/router';

export interface MatrixCell {
  action: DeliveryAction;
  /** Where to deliver, or null for LOG_ONLY. */
  channel: Channel | null;
  /** Human-readable reason for audit logs. */
  reason: string;
  /** Hint for the queue about when to retry delivery. */
  queueHint?: QueueHint;
}

export type Channel = 'push' | 'in_app_toast' | 'in_app_banner' | 'digest' | 'email' | 'audit_log';
export type QueueHint = 'next_break' | 'on_return' | 'next_batch_window' | 'immediate_retry';

export const ROUTING_MATRIX: Record<Urgency, Record<FlowState, MatrixCell>> = {
  INTERRUPT: {
    AVAILABLE: {
      action: 'DELIVER_NOW',
      channel: 'push',
      reason: 'Interrupt urgency while user is available — deliver immediately.',
    },
    FOCUSED: {
      action: 'QUEUE',
      channel: 'in_app_toast',
      reason: 'Interrupt urgency suppressed during focused work — queue for next break.',
      queueHint: 'next_break',
    },
    DEEP_WORK: {
      action: 'QUEUE',
      channel: 'in_app_toast',
      reason: 'Interrupt urgency suppressed during deep work — queue for next break.',
      queueHint: 'next_break',
    },
    AWAY: {
      action: 'QUEUE',
      channel: 'push',
      reason: 'User away — queue for delivery on return.',
      queueHint: 'on_return',
    },
    OFFLINE: {
      action: 'QUEUE',
      channel: 'push',
      reason: 'User offline — queue for delivery on return.',
      queueHint: 'on_return',
    },
  },

  BATCH: {
    AVAILABLE: {
      action: 'QUEUE',
      channel: 'digest',
      reason: 'Batch urgency — always queue for batch delivery window.',
      queueHint: 'next_batch_window',
    },
    FOCUSED: {
      action: 'QUEUE',
      channel: 'digest',
      reason: 'Batch urgency — queue for batch delivery window.',
      queueHint: 'next_batch_window',
    },
    DEEP_WORK: {
      action: 'QUEUE',
      channel: 'digest',
      reason: 'Batch urgency — queue for batch delivery window.',
      queueHint: 'next_batch_window',
    },
    AWAY: {
      action: 'QUEUE',
      channel: 'digest',
      reason: 'Batch urgency — queue for batch delivery window.',
      queueHint: 'next_batch_window',
    },
    OFFLINE: {
      action: 'QUEUE',
      channel: 'digest',
      reason: 'Batch urgency — queue for batch delivery window.',
      queueHint: 'next_batch_window',
    },
  },

  SILENT: {
    AVAILABLE: {
      action: 'LOG_ONLY',
      channel: 'audit_log',
      reason: 'Silent urgency — log only, no user-facing delivery.',
    },
    FOCUSED: {
      action: 'LOG_ONLY',
      channel: 'audit_log',
      reason: 'Silent urgency — log only, no user-facing delivery.',
    },
    DEEP_WORK: {
      action: 'LOG_ONLY',
      channel: 'audit_log',
      reason: 'Silent urgency — log only, no user-facing delivery.',
    },
    AWAY: {
      action: 'LOG_ONLY',
      channel: 'audit_log',
      reason: 'Silent urgency — log only, no user-facing delivery.',
    },
    OFFLINE: {
      action: 'LOG_ONLY',
      channel: 'audit_log',
      reason: 'Silent urgency — log only, no user-facing delivery.',
    },
  },
};

/**
 * Cooldown configuration per urgency level.
 * If the same escalation key was delivered within this window,
 * DELIVER_NOW degrades to QUEUE to avoid nagging.
 */
export const COOLDOWN_MS: Record<Urgency, number> = {
  INTERRUPT: 5 * 60 * 1000,   // 5 minutes
  BATCH: 60 * 60 * 1000,      // 1 hour
  SILENT: 0,                  // no cooldown — never delivers anyway
};

/**
 * Maximum deferrals before a queued item is promoted to forced delivery
 * on the next availability window. The router checks this against
 * the escalation's deferralCount.
 */
export const MAX_DEFERRALS_BEFORE_FORCE = 3;
