/**
 * Urgency Rules Configuration
 *
 * Defines the default classification for escalations based on
 * source and event type. These are the baseline rules that the
 * classifier consults before applying any contextual overrides.
 */

export type UrgencyLevel = 'INTERRUPT' | 'BATCH' | 'SILENT';

export type FlowState = 'deep-work' | 'available' | 'focus' | 'away' | 'off-hours';

export interface EscalationEvent {
  source: string;          // e.g. 'github', 'ci-pipeline', 'jira'
  eventType: string;       // e.g. 'build-failed', 'pr-review-requested'
  severity?: 'critical' | 'high' | 'medium' | 'low' | 'info';
  isBlocker?: boolean;     // does this block downstream work?
  requiresApproval?: boolean;
  message?: string;
  timestamp?: string;
  metadata?: Record<string, unknown>;
}

export interface ClassificationContext {
  flowState: FlowState;
  timeOfDay?: 'morning' | 'afternoon' | 'evening' | 'night';
  isWeekend?: boolean;
  pendingBatchCount?: number;  // how many BATCH items are queued
  userOverrides?: UserOverride[];
}

export interface UserOverride {
  source?: string;
  eventType?: string;
  urgency: UrgencyLevel;
  condition?: {
    flowState?: FlowState[];
    timeOfDay?: string[];
    isWeekend?: boolean;
  };
}

interface RuleEntry {
  urgency: UrgencyLevel;
  reason: string;
}

/**
 * The core rules table. Keys are `${source}:${eventType}`.
 * A fallback `default:*` entry handles unmatched events.
 */
export const URGENCY_RULES: Record<string, RuleEntry> = {
  // ── CI/CD Pipeline ──────────────────────────────────────────
  'ci-pipeline:build-failed': {
    urgency: 'INTERRUPT',
    reason: 'Failed build blocks integration; requires immediate triage',
  },
  'ci-pipeline:build-succeeded': {
    urgency: 'SILENT',
    reason: 'Successful build is informational; logged for audit',
  },
  'ci-pipeline:deploy-failed': {
    urgency: 'INTERRUPT',
    reason: 'Deployment failure may affect production',
  },
  'ci-pipeline:deploy-succeeded': {
    urgency: 'BATCH',
    reason: 'Deployment completion is a status update',
  },
  'ci-pipeline:deploy-started': {
    urgency: 'SILENT',
    reason: 'Deployment initiation is informational',
  },
  'ci-pipeline:test-failed': {
    urgency: 'INTERRUPT',
    reason: 'Test failure may block merge',
  },
  'ci-pipeline:test-flaky': {
    urgency: 'BATCH',
    reason: 'Flaky tests are notable but not blocking',
  },

  // ── Code Review / Pull Requests ─────────────────────────────
  'github:pr-review-requested': {
    urgency: 'BATCH',
    reason: 'Review requests can be batched; not typically blocking',
  },
  'github:pr-review-requested-urgent': {
    urgency: 'INTERRUPT',
    reason: 'Explicitly marked urgent review request',
  },
  'github:pr-comment': {
    urgency: 'SILENT',
    reason: 'Comments are informational unless mentioned',
  },
  'github:pr-mention': {
    urgency: 'BATCH',
    reason: 'Mentions should be seen but can be batched',
  },
  'github:pr-approved': {
    urgency: 'SILENT',
    reason: 'Approval is informational',
  },
  'github:pr-changes-requested': {
    urgency: 'BATCH',
    reason: 'Changes requested need attention but not immediate',
  },
  'github:pr-merged': {
    urgency: 'SILENT',
    reason: 'Merge completion is informational',
  },
  'github:pr-conflict': {
    urgency: 'BATCH',
    reason: 'Merge conflict needs resolution but not interrupting',
  },

  // ── Task Management (Jira / Linear / etc.) ──────────────────
  'jira:task-assigned': {
    urgency: 'BATCH',
    reason: 'New assignment can be reviewed in batch',
  },
  'jira:task-blocked': {
    urgency: 'INTERRUPT',
    reason: 'Blocked task impedes progress; needs attention',
  },
  'jira:task-completed': {
    urgency: 'SILENT',
    reason: 'Completion is informational',
  },
  'jira:task-comment': {
    urgency: 'SILENT',
    reason: 'Task comments are informational unless mentioned',
  },
  'jira:task-mention': {
    urgency: 'BATCH',
    reason: 'Mentions should be seen but can be batched',
  },
  'jira:sprint-starting': {
    urgency: 'BATCH',
    reason: 'Sprint transitions are status updates',
  },
  'jira:sprint-ending': {
    urgency: 'BATCH',
    reason: 'Sprint wrap-up is a summary event',
  },
  'jira:deadline-approaching': {
    urgency: 'BATCH',
    reason: 'Deadline warnings should be batched unless imminent',
  },
  'jira:deadline-imminent': {
    urgency: 'INTERRUPT',
    reason: 'Imminent deadline requires immediate attention',
  },

  // ── Approvals ───────────────────────────────────────────────
  'approval:manual-required': {
    urgency: 'INTERRUPT',
    reason: 'Manual approval gates block workflow progress',
  },
  'approval:auto-granted': {
    urgency: 'SILENT',
    reason: 'Auto-approval is informational',
  },
  'approval:expired': {
    urgency: 'BATCH',
    reason: 'Expired approval needs re-request but not immediate',
  },

  // ── System / Infrastructure ─────────────────────────────────
  'system:error': {
    urgency: 'INTERRUPT',
    reason: 'System errors may require intervention',
  },
  'system:warning': {
    urgency: 'BATCH',
    reason: 'Warnings are notable but not immediately actionable',
  },
  'system:info': {
    urgency: 'SILENT',
    reason: 'Informational system events are log-only',
  },
  'system:outage': {
    urgency: 'INTERRUPT',
    reason: 'Outage affects availability; requires immediate response',
  },
  'system:recovery': {
    urgency: 'BATCH',
    reason: 'Recovery is a status update',
  },

  // ── Scheduled / Digests ─────────────────────────────────────
  'scheduler:daily-summary': {
    urgency: 'BATCH',
    reason: 'Summaries are inherently batch-oriented',
  },
  'scheduler:weekly-digest': {
    urgency: 'BATCH',
    reason: 'Digests are inherently batch-oriented',
  },
  'scheduler:reminder': {
    urgency: 'BATCH',
    reason: 'Reminders can be batched unless time-critical',
  },

  // ── Chat / Messaging ────────────────────────────────────────
  'slack:direct-message': {
    urgency: 'BATCH',
    reason: 'DMs should be seen but can be batched',
  },
  'slack:direct-message-urgent': {
    urgency: 'INTERRUPT',
    reason: 'Explicitly urgent DM',
  },
  'slack:channel-mention': {
    urgency: 'BATCH',
    reason: 'Channel mentions can be batched',
  },
  'slack:channel-message': {
    urgency: 'SILENT',
    reason: 'General channel traffic is informational',
  },

  // ── Default Fallback ────────────────────────────────────────
  'default:*': {
    urgency: 'BATCH',
    reason: 'Unknown events default to batch for safety',
  },
};

/**
 * Flow state downgrade rules.
 * When a user is in a protected flow state, certain INTERRUPT
 * events may be downgraded to BATCH if they're not truly blocking.
 */
export const FLOW_STATE_DOWNGRADES: Partial<Record<FlowState, string[]>> = {
  'deep-work': [
    // These INTERRUPT events get downgraded to BATCH during deep work
    // because they're important but not truly blocking
    'github:pr-review-requested',
    'jira:task-assigned',
    'jira:deadline-approaching',
    'slack:direct-message',
    'approval:expired',
  ],
  'focus': [
    'github:pr-review-requested',
    'jira:task-assigned',
    'slack:direct-message',
  ],
};

/**
 * Events that are NEVER downgraded regardless of flow state.
 * These represent true blockers or critical situations.
 */
export const NON_DOWNGRADABLE_EVENTS: string[] = [
  'ci-pipeline:build-failed',
  'ci-pipeline:deploy-failed',
  'ci-pipeline:test-failed',
  'system:outage',
  'system:error',
  'approval:manual-required',
  'jira:task-blocked',
  'jira:deadline-imminent',
  'github:pr-review-requested-urgent',
  'slack:direct-message-urgent',
];

/**
 * Off-hours behavior: during off-hours, most INTERRUPT events
 * become BATCH (to be delivered when user returns), except those
 * listed here which remain INTERRUPT.
 */
export const OFF_HOURS_PERSIST_INTERRUPT: string[] = [
  'system:outage',
  'system:error',
  'ci-pipeline:deploy-failed',
];
