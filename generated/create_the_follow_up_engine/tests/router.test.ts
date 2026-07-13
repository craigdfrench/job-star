// tests/router.test.ts
import { describe, it, expect } from 'vitest';
import { routeChannel } from '../src/services/router';
import { UrgencyLevel } from '../src/config/urgency-rules';
import type { FlowState } from '../src/services/flow-state';
import type { UserPreferences } from '../src/config/user-preferences';

// Flow state values as defined in the flow-state tracker.
// Adjust these literals if the FlowState type uses different string values.
const FLOW_AVAILABLE: FlowState = 'available' as FlowState;
const FLOW_FOCUS: FlowState = 'focus' as FlowState;
const FLOW_AWAY: FlowState = 'away' as FlowState;

function defaultPrefs(): UserPreferences {
  return {
    interruptDuringFocus: false,
    batchIntervalMinutes: 15,
    silentDigestEnabled: true,
  } as UserPreferences;
}

describe('Channel Router', () => {
  describe('interrupt urgency', () => {
    it('routes to push channel when user is available', () => {
      const action = routeChannel(UrgencyLevel.Interrupt, FLOW_AVAILABLE, defaultPrefs());
      expect(action.channel).toBe('push');
      expect(action.deferred).toBe(false);
    });

    it('routes to push channel when user is in focus if prefs allow', () => {
      const prefs = defaultPrefs();
      prefs.interruptDuringFocus = true;
      const action = routeChannel(UrgencyLevel.Interrupt, FLOW_FOCUS, prefs);
      expect(action.channel).toBe('push');
      expect(action.deferred).toBe(false);
    });

    it('defers to batch queue when user is in focus and prefs disallow interrupt', () => {
      const action = routeChannel(UrgencyLevel.Interrupt, FLOW_FOCUS, defaultPrefs());
      expect(action.channel).toBe('batch');
      expect(action.deferred).toBe(true);
    });

    it('defers to batch queue when user is away', () => {
      const action = routeChannel(UrgencyLevel.Interrupt, FLOW_AWAY, defaultPrefs());
      expect(action.channel).toBe('batch');
      expect(action.deferred).toBe(true);
    });
  });

  describe('batch urgency', () => {
    it('routes to batch channel when user is available', () => {
      const action = routeChannel(UrgencyLevel.Batch, FLOW_AVAILABLE, defaultPrefs());
      expect(action.channel).toBe('batch');
      expect(action.deferred).toBe(false);
    });

    it('routes to batch channel when user is in focus', () => {
      const action = routeChannel(UrgencyLevel.Batch, FLOW_FOCUS, defaultPrefs());
      expect(action.channel).toBe('batch');
      expect(action.deferred).toBe(false);
    });

    it('routes to batch channel when user is away', () => {
      const action = routeChannel(UrgencyLevel.Batch, FLOW_AWAY, defaultPrefs());
      expect(action.channel).toBe('batch');
      expect(action.deferred).toBe(false);
    });
  });

  describe('silent urgency', () => {
    it('routes to digest channel when user is available', () => {
      const action = routeChannel(UrgencyLevel.Silent, FLOW_AVAILABLE, defaultPrefs());
      expect(action.channel).toBe('digest');
      expect(action.deferred).toBe(false);
    });

    it('routes to digest channel when user is in focus', () => {
      const action = routeChannel(UrgencyLevel.Silent, FLOW_FOCUS, defaultPrefs());
      expect(action.channel).toBe('digest');
      expect(action.deferred).toBe(false);
    });

    it('routes to digest channel when user is away', () => {
      const action = routeChannel(UrgencyLevel.Silent, FLOW_AWAY, defaultPrefs());
      expect(action.channel).toBe('digest');
      expect(action.deferred).toBe(false);
    });

    it('drops to noop when silent digest is disabled in prefs', () => {
      const prefs = defaultPrefs();
      prefs.silentDigestEnabled = false;
      const action = routeChannel(UrgencyLevel.Silent, FLOW_AVAILABLE, prefs);
      expect(action.channel).toBe('noop');
    });
  });

  describe('preference overrides', () => {
    it('respects interruptDuringFocus = true even for batch urgency (no upgrade)', () => {
      // Batch should never upgrade to push regardless of pref — only interrupt does.
      const prefs = defaultPrefs();
      prefs.interruptDuringFocus = true;
      const action = routeChannel(UrgencyLevel.Batch, FLOW_FOCUS, prefs);
      expect(action.channel).toBe('batch');
    });
  });
});
