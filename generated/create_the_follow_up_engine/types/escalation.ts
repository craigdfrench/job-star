import {
  UrgencyLevel,
  DeliveryStatus,
  Criticality,
  FlowStateSnapshot,
} from './urgency';

/**
 * Identifies which Job-Star component raised this escalation.
 * Using a string union rather than an enum so external integrations
 * can introduce new sources without a code change to the core types —
 * unknown sources are accepted but flagged in metadata for triage.
 */
export type SourceComponent =
  | 'deadline-tracker'
  | 'ci-watcher'
  | 'blocker-detector'
  | 'review-queue'
  | 'dependency-graph'
  | 'schedule-monitor'
  | 'external-webhook'
  | 'manual'
  | string; // allow forward-compatible unknown sources

/**
 * Structured context payload. Free-form but with a few conventional
 * keys the classifier and UI know how to render. Anything else lives
 * in `metadata`.
 */
export interface EscalationContext {
  /** Stable identifier for the *thing* being escalated (job id, PR url, etc). */
  subjectId?: string;
  /** Human label for the subject, e.g. "PR #142: refactor auth flow". */
  subjectLabel?: string;
  /** URL or deep link the user can navigate to. */
  link?: string;
  /** Other escalation ids this one is related to (grouping, dedup). */
  relatedEscalationIds?: string[];
  /** Short reason code the classifier can pattern-match on. */
  reasonCode?: string;
  [key: string]: unknown;
}

/**
 * The core escalation event.
 *
 * Originating components populate the "input" fields (sourceComponent,
 * message, context, timestamp, suggestedUrgency, metadata). The engine
 * then fills in the "engine" fields (id, assignedUrgency, criticality,
 * deliveryStatus, flowStateAtClassification, classifiedAt, history).
 */
export interface EscalationEvent {
  // ---- Engine-assigned identity ----
  /** UUID v4, assigned on intake. */
  id: string;

  // ---- Input fields (populated by the source component) ----
  /** Which component raised this. */
  sourceComponent: SourceComponent;
  /** One-line human-readable summary. Keep under ~120 chars for UI. */
  message: string;
  /** Structured details about the subject of the escalation. */
  context: EscalationContext;
  /** Epoch ms when the *situation* occurred (may predate intake). */
  timestamp: number;
  /**
   * The source's own guess at urgency. The classifier may override this.
   * Sources tend to over-estimate their own importance; the classifier
   * exists in part to correct for that.
   */
  suggestedUrgency: UrgencyLevel;
  /** Arbitrary extra data — audit trail, raw payload, tags. */
  metadata?: Record<string, unknown>;

  // ---- Engine-assigned classification & delivery ----
  /** What the classifier actually decided. Null until classified. */
  assignedUrgency?: UrgencyLevel;
  /** Only meaningful when assignedUrgency === INTERRUPT. */
  criticality?: Criticality;
  /** Current lifecycle state. Starts PENDING. */
  deliveryStatus: DeliveryStatus;
  /** Flow state captured at classification time, for audit & re-eval. */
  flowStateAtClassification?: FlowStateSnapshot;
  /** Epoch ms when the classifier ran. */
  classifiedAt?: number;
  /** Epoch ms when last delivered/surfaced. */
  deliveredAt?: number;
  /** Epoch ms when the user acknowledged it. */
  acknowledgedAt?: number;

  // ---- Audit trail ----
  /**
   * Append-only log of lifecycle transitions. Each entry is a terse
   * record so we can reconstruct *why* an escalation ended up where
   * it did — essential for tuning the classifier later.
   */
  history?: EscalationHistoryEntry[];
}

export interface EscalationHistoryEntry {
  at: number;
  fromStatus: DeliveryStatus;
  toStatus: DeliveryStatus;
  /** What triggered the transition: "classifier", "dispatcher", "user-ack", "expiry-timer", etc. */
  actor: string;
  /** Optional human-readable note. */
  note?: string;
}

/**
 * Convenience type guard — has this escalation been classified yet?
 */
export function isClassified(e: EscalationEvent): boolean {
  return e.assignedUrgency !== undefined && e.deliveryStatus !== DeliveryStatus.PENDING;
}

/**
 * Convenience: is the escalation still "live" (could still be delivered)?
 * RESOLVED, EXPIRED, and ACKNOWLEDGED are terminal-ish.
 */
export function isLive(e: EscalationEvent): boolean {
  return (
    e.deliveryStatus !== DeliveryStatus.RESOLVED &&
    e.deliveryStatus !== DeliveryStatus.EXPIRED &&
    e.deliveryStatus !== DeliveryStatus.ACKNOWLEDGED
  );
}
