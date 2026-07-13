/**
 * Configuration for flow state detection thresholds.
 *
 * All values are in milliseconds.
 */
export interface FlowThresholds {
  /**
   * Idle time after which the user is considered AWAY.
   * Default: 5 minutes. If no activity for this long, user has stepped away.
   */
  awayThreshold: number;

  /**
   * Idle time after which the user is considered FOCUSED (reading/thinking).
   * If the user's last activity was more than this long ago but less than
   * awayThreshold, they're likely reading output or thinking — treat as FOCUSED.
   * Default: 30 seconds.
   */
  focusedIdleThreshold: number;

  /**
   * Duration of sustained activity that qualifies as a focused work session.
   * If the user has been active (multiple activity events) for at least this
   * long within the activity window, they're in FOCUSED flow — even if they
   * just typed something moments ago.
   * Default: 10 minutes.
   */
  focusedSessionThreshold: number;

  /**
   * Rolling window size for tracking activity events.
   * Activity timestamps older than this are pruned.
   * Should be >= focusedSessionThreshold.
   * Default: 15 minutes.
   */
  activityWindowMs: number;
}

/**
 * Default flow state thresholds.
 *
 * These are conservative defaults. Users can override via config.
 */
export const DEFAULT_FLOW_THRESHOLDS: FlowThresholds = {
  awayThreshold: 5 * 60 * 1000,        // 5 minutes
  focusedIdleThreshold: 30 * 1000,      // 30 seconds
  focusedSessionThreshold: 10 * 60 * 1000, // 10 minutes
  activityWindowMs: 15 * 60 * 1000,     // 15 minutes
};

/**
 * Load flow thresholds from config, falling back to defaults.
 *
 * In the full system this would read from a config file or environment.
 * For now, it merges any provided overrides with defaults.
 */
export function loadFlowThresholds(
  overrides?: Partial<FlowThresholds>
): FlowThresholds {
  return { ...DEFAULT_FLOW_THRESHOLDS, ...overrides };
}
