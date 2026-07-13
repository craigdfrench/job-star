/**
 * Flow State Tracker
 *
 * Tracks user activity signals and determines the user's current flow state:
 * AVAILABLE, FOCUSED, or AWAY.
 *
 * Signals used:
 *   1. Rolling window of activity timestamps (detects sustained work sessions)
 *   2. Explicit "do not disturb" flag
 *   3. Idle timeout (time since last activity)
 *
 * Usage:
 *   const tracker = new FlowStateTracker();
 *   // Call recordActivity() on any terminal/editor interaction
 *   tracker.recordActivity();
 *   // Check state before surfacing notifications
 *   const state = tracker.getFlowState();
 */

import {
  FlowThresholds,
  loadFlowThresholds,
} from '../config/flow-thresholds';

/**
 * The three flow states the tracker can report.
 */
export enum FlowState {
  /** User is actively interacting and interruptible. */
  AVAILABLE = 'AVAILABLE',

  /** User is in deep work or a reading/thinking pause. Prefer not to interrupt. */
  FOCUSED = 'FOCUSED',

  /** User is idle beyond the away threshold. Not at the keyboard. */
  AWAY = 'AWAY',
}

/**
 * Details about the current flow state, useful for logging and debugging.
 */
export interface FlowStateResult {
  state: FlowState;
  /** Milliseconds since last activity. null if no activity recorded yet. */
  idleMs: number | null;
  /** Whether the DND flag is set. */
  dndActive: boolean;
  /** Duration of the current sustained activity session in ms, if any. */
  sessionDurationMs: number | null;
  /** Human-readable reason for the current state classification. */
  reason: string;
}

export class FlowStateTracker {
  private thresholds: FlowThresholds;
  private activityTimestamps: number[] = [];
  private dndFlag: boolean = false;
  private lastActivity: number | null = null;

  constructor(thresholds?: Partial<FlowThresholds>) {
    this.thresholds = loadFlowThresholds(thresholds);
  }

  /**
   * Record a user activity event.
   *
   * Call this on any signal that indicates the user is at the keyboard:
   * terminal input, editor keystroke, command execution, etc.
   */
  recordActivity(timestamp: number = Date.now()): void {
    this.lastActivity = timestamp;
    this.activityTimestamps.push(timestamp);
    this.pruneOldActivity(timestamp);
  }

  /**
   * Set or clear the explicit "do not disturb" flag.
   *
   * When set, getFlowState() will always return FOCUSED regardless
   * of activity signals. This is a hard override.
   */
  setDoNotDisturb(enabled: boolean): void {
    this.dndFlag = enabled;
  }

  /**
   * Returns whether the DND flag is currently set.
   */
  isDoNotDisturb(): boolean {
    return this.dndFlag;
  }

  /**
   * Get the current flow state.
   *
   * Decision order (first match wins):
   *   1. DND flag set → FOCUSED
   *   2. No activity ever recorded → AWAY
   *   3. Idle > awayThreshold → AWAY
   *   4. Sustained session >= focusedSessionThreshold → FOCUSED
   *   5. Idle > focusedIdleThreshold (but < awayThreshold) → FOCUSED
   *   6. Otherwise → AVAILABLE
   */
  getFlowState(): FlowState {
    return this.evaluate(Date.now()).state;
  }

  /**
   * Get detailed flow state information including reasoning.
   */
  getFlowStateDetail(): FlowStateResult {
    return this.evaluate(Date.now());
  }

  /**
   * Core evaluation logic. Separated from getFlowState() so we can
   * compute everything once and return details.
   */
  private evaluate(now: number): FlowStateResult {
    // 1. DND override
    if (this.dndFlag) {
      return {
        state: FlowState.FOCUSED,
        idleMs: this.lastActivity !== null ? now - this.lastActivity : null,
        dndActive: true,
        sessionDurationMs: this.getSessionDuration(now),
        reason: 'Do not disturb flag is set',
      };
    }

    // 2. No activity ever recorded
    if (this.lastActivity === null) {
      return {
        state: FlowState.AWAY,
        idleMs: null,
        dndActive: false,
        sessionDurationMs: null,
        reason: 'No activity has been recorded yet',
      };
    }

    const idleMs = now - this.lastActivity;
    const sessionDurationMs = this.getSessionDuration(now);

    // 3. Idle beyond away threshold
    if (idleMs >= this.thresholds.awayThreshold) {
      return {
        state: FlowState.AWAY,
        idleMs,
        dndActive: false,
        sessionDurationMs,
        reason: `Idle for ${Math.round(idleMs / 1000)}s (exceeds away threshold of ${Math.round(this.thresholds.awayThreshold / 1000)}s)`,
      };
    }

    // 4. Sustained work session
    if (
      sessionDurationMs !== null &&
      sessionDurationMs >= this.thresholds.focusedSessionThreshold
    ) {
      return {
        state: FlowState.FOCUSED,
        idleMs,
        dndActive: false,
        sessionDurationMs,
        reason: `Sustained activity session of ${Math.round(sessionDurationMs / 1000)}s (exceeds focused session threshold of ${Math.round(this.thresholds.focusedSessionThreshold / 1000)}s)`,
      };
    }

    // 5. Reading/thinking pause
    if (idleMs >= this.thresholds.focusedIdleThreshold) {
      return {
        state: FlowState.FOCUSED,
        idleMs,
        dndActive: false,
        sessionDurationMs,
        reason: `Idle for ${Math.round(idleMs / 1000)}s (likely reading/thinking, exceeds focused idle threshold of ${Math.round(this.thresholds.focusedIdleThreshold / 1000)}s)`,
      };
    }

    // 6. Actively available
    return {
      state: FlowState.AVAILABLE,
      idleMs,
      dndActive: false,
      sessionDurationMs,
      reason: `Active within the last ${Math.round(idleMs / 1000)}s and no sustained session detected`,
    };
  }

  /**
   * Calculate the duration of the current sustained activity session.
   *
   * A "session" is a contiguous block of activity where no gap between
   * consecutive events exceeds the awayThreshold. If the most recent gap
   * exceeds awayThreshold, we're in a new session starting from the first
   * event after that gap.
   *
   * Returns null if there are fewer than 2 activity events (can't establish
   * a session) or if the session hasn't meaningfully started.
   */
  private getSessionDuration(now: number): number | null {
    this.pruneOldActivity(now);

    if (this.activityTimestamps.length === 0) {
      return null;
    }

    // Walk backwards from the most recent activity to find where the
    // current session started (first event with no gap > awayThreshold
    // between it and the next event).
    const events = this.activityTimestamps;
    let sessionStartIdx = events.length - 1;

    for (let i = events.length - 1; i > 0; i--) {
      const gap = events[i] - events[i - 1];
      if (gap > this.thresholds.awayThreshold) {
        // Gap too large — session starts at events[i]
        sessionStartIdx = i;
        break;
      }
      sessionStartIdx = i - 1;
    }

    const sessionStart = events[sessionStartIdx];
    const duration = this.lastActivity !== null
      ? this.lastActivity - sessionStart
      : 0;

    // Need at least 2 events to call it a session
    if (events.length - sessionStartIdx < 2) {
      return null;
    }

    return duration;
  }

  /**
   * Remove activity timestamps older than the activity window.
   */
  private pruneOldActivity(now: number): void {
    const cutoff = now - this.thresholds.activityWindowMs;
    // Since timestamps are pushed in order, we can find the first
    // index that's within the window and slice from there.
    const firstValidIdx = this.activityTimestamps.findIndex(
      (ts) => ts >= cutoff
    );
    if (firstValidIdx === -1) {
      // All events are old
      this.activityTimestamps = [];
    } else if (firstValidIdx > 0) {
      this.activityTimestamps = this.activityTimestamps.slice(firstValidIdx);
    }
  }

  /**
   * Reset all state. Useful for testing or when switching users.
   */
  reset(): void {
    this.activityTimestamps = [];
    this.dndFlag = false;
    this.lastActivity = null;
  }

  /**
   * Update thresholds at runtime.
   */
  setThresholds(overrides: Partial<FlowThresholds>): void {
    this.thresholds = loadFlowThresholds({
      ...this.thresholds,
      ...overrides,
    });
  }
}
