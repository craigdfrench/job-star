// tests/classifier.test.ts
import { describe, it, expect } from 'vitest';
import { classifyUrgency } from '../src/services/classifier';
import { UrgencyLevel } from '../src/config/urgency-rules';
import type { EscalationEvent } from '../src/config/urgency-rules';

describe('Urgency Classifier', () => {
  // Helper to build a minimal valid escalation event
  function makeEvent(overrides: Partial<EscalationEvent> = {}): EscalationEvent {
    return {
      id: 'evt-001',
      userId: 'user-1',
      jobId: 'job-1',
      type: 'deadline',
      source: 'scheduler',
      payload: {},
      createdAt: new Date('2025-01-01T00:00:00Z'),
      ...overrides,
    } as EscalationEvent;
  }

  describe('interrupt-level events', () => {
    it('classifies an overdue hard deadline as interrupt', () => {
      const event = makeEvent({
        type: 'deadline',
        payload: { overdue: true, severity: 'critical' },
      });
      expect(classifyUrgency(event)).toBe(UrgencyLevel.Interrupt);
    });

    it('classifies a blocking dependency failure as interrupt', () => {
      const event = makeEvent({
        type: 'dependency-failed',
        payload: { blocks: ['job-2'], severity: 'critical' },
      });
      expect(classifyUrgency(event)).toBe(UrgencyLevel.Interrupt);
    });

    it('classifies a manual escalation flagged urgent as interrupt', () => {
      const event = makeEvent({
        type: 'manual-escalation',
        payload: { flaggedUrgent: true },
      });
      expect(classifyUrgency(event)).toBe(UrgencyLevel.Interrupt);
    });
  });

  describe('batch-level events', () => {
    it('classifies a soft deadline approaching as batch', () => {
      const event = makeEvent({
        type: 'deadline',
        payload: { overdue: false, hoursRemaining: 6, severity: 'warning' },
      });
      expect(classifyUrgency(event)).toBe(UrgencyLevel.Batch);
    });

    it('classifies a status change requiring acknowledgement as batch', () => {
      const event = makeEvent({
        type: 'status-change',
        payload: { requiresAck: true, severity: 'warning' },
      });
      expect(classifyUrgency(event)).toBe(UrgencyLevel.Batch);
    });

    it('classifies a non-blocking dependency warning as batch', () => {
      const event = makeEvent({
        type: 'dependency-warning',
        payload: { blocks: [], severity: 'warning' },
      });
      expect(classifyUrgency(event)).toBe(UrgencyLevel.Batch);
    });
  });

  describe('silent-level events', () => {
    it('classifies an informational status update as silent', () => {
      const event = makeEvent({
        type: 'status-change',
        payload: { requiresAck: false, severity: 'info' },
      });
      expect(classifyUrgency(event)).toBe(UrgencyLevel.Silent);
    });

    it('classifies a completed-job notification as silent', () => {
      const event = makeEvent({
        type: 'job-completed',
        payload: { severity: 'info' },
      });
      expect(classifyUrgency(event)).toBe(UrgencyLevel.Silent);
    });

    it('classifies a metrics snapshot as silent', () => {
      const event = makeEvent({
        type: 'metrics-snapshot',
        payload: { severity: 'info' },
      });
      expect(classifyUrgency(event)).toBe(UrgencyLevel.Silent);
    });
  });

  describe('edge cases', () => {
    it('defaults unknown event types to silent', () => {
      const event = makeEvent({ type: 'unknown-type', payload: {} });
      expect(classifyUrgency(event)).toBe(UrgencyLevel.Silent);
    });

    it('treats missing severity as info (silent)', () => {
      const event = makeEvent({ type: 'status-change', payload: {} });
      expect(classifyUrgency(event)).toBe(UrgencyLevel.Silent);
    });

    it('upgrades warning to interrupt when overdue flag is set', () => {
      const event = makeEvent({
        type: 'deadline',
        payload: { overdue: true, severity: 'warning' },
      });
      expect(classifyUrgency(event)).toBe(UrgencyLevel.Interrupt);
    });
  });
});
