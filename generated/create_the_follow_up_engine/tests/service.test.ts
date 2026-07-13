// tests/service.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NotificationService } from '../src/services/notification-service';
import { UrgencyLevel } from '../src/config/urgency-rules';
import type { EscalationEvent } from '../src/config/urgency-rules';
import type { FlowState } from '../src/services/flow-state';

// We mock the flow-state tracker so the service test doesn't depend on
// real activity signals. The service should call getFlowState(userId)
// and we control what it returns per test.
const mockGetFlowState = vi.fn<(userId: string) => FlowState>();

// Mock channel implementations — each is a spy so we can assert delivery.
const pushChannel = { deliver: vi.fn(), name: 'push' };
const batchChannel = { deliver: vi.fn(), name: 'batch' };
const digestChannel = { deliver: vi.fn(), name: 'digest' };

function makeEvent(overrides: Partial<EscalationEvent> = {}): EscalationEvent {
  return {
    id: 'evt-' + Math.random().toString(36).slice(2, 8),
    userId: 'user-1',
    jobId: 'job-1',
    type: 'deadline',
    source: 'scheduler',
    payload: {},
    createdAt: new Date(),
    ...overrides,
  } as EscalationEvent;
}

function makeService(): NotificationService {
  return new NotificationService({
    getFlowState: mockGetFlowState,
    channels: {
      push: pushChannel,
      batch: batchChannel,
      digest: digestChannel,
    },
    preferences: {
      interruptDuringFocus: false,
      batchIntervalMinutes: 15,
      silentDigestEnabled: true,
    },
  });
}

describe('Notification Service (integration)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('interrupt escalation while available', () => {
    it('delivers immediately via push channel', async () => {
      mockGetFlowState.mockReturnValue('available' as FlowState);
      const service = makeService();

      const event = makeEvent({
        type: 'deadline',
        payload: { overdue: true, severity: 'critical' },
      });

      const result = await service.handleEscalation(event);

      expect(result.urgency).toBe(UrgencyLevel.Interrupt);
      expect(result.channel).toBe('push');
      expect(result.deferred).toBe(false);
      expect(pushChannel.deliver).toHaveBeenCalledTimes(1);
      expect(pushChannel.deliver).toHaveBeenCalledWith(expect.objectContaining({ id: event.id }));
      expect(batchChannel.deliver).not.toHaveBeenCalled();
      expect(digestChannel.deliver).not.toHaveBeenCalled();
    });
  });

  describe('interrupt escalation while in focus', () => {
    it('defers to batch channel (no push interrupt)', async () => {
      mockGetFlowState.mockReturnValue('focus' as FlowState);
      const service = makeService();

      const event = makeEvent({
        type: 'deadline',
        payload: { overdue: true, severity: 'critical' },
      });

      const result = await service.handleEscalation(event);

      expect(result.urgency).toBe(UrgencyLevel.Interrupt);
      expect(result.channel).toBe('batch');
      expect(result.deferred).toBe(true);
      expect(pushChannel.deliver).not.toHaveBeenCalled();
      expect(batchChannel.deliver).toHaveBeenCalledTimes(1);
    });
  });

  describe('batch escalation', () => {
    it('routes to batch channel regardless of flow state', async () => {
      mockGetFlowState.mockReturnValue('focus' as FlowState);
      const service = makeService();

      const event = makeEvent({
        type: 'status-change',
        payload: { requiresAck: true, severity: 'warning' },
      });

      const result = await service.handleEscalation(event);

      expect(result.urgency).toBe(UrgencyLevel.Batch);
      expect(result.channel).toBe('batch');
      expect(batchChannel.deliver).toHaveBeenCalledTimes(1);
      expect(pushChannel.deliver).not.toHaveBeenCalled();
    });
  });

  describe('silent escalation', () => {
    it('routes to digest channel', async () => {
      mockGetFlowState.mockReturnValue('available' as FlowState);
      const service = makeService();

      const event = makeEvent({
        type: 'job-completed',
        payload: { severity: 'info' },
      });

      const result = await service.handleEscalation(event);

      expect(result.urgency).toBe(UrgencyLevel.Silent);
      expect(result.channel).toBe('digest');
      expect(digestChannel.deliver).toHaveBeenCalledTimes(1);
      expect(pushChannel.deliver).not.toHaveBeenCalled();
      expect(batchChannel.deliver).not.toHaveBeenCalled();
    });
  });

  describe('flow state is queried once per escalation', () => {
    it('calls getFlowState exactly once with the event userId', async () => {
      mockGetFlowState.mockReturnValue('available' as FlowState);
      const service = makeService();

      await service.handleEscalation(makeEvent({ userId: 'user-42' }));

      expect(mockGetFlowState).toHaveBeenCalledTimes(1);
      expect(mockGetFlowState).toHaveBeenCalledWith('user-42');
    });
  });

  describe('multiple escalations route independently', () => {
    it('delivers each event to the correct channel based on its own urgency', async () => {
      mockGetFlowState.mockReturnValue('available' as FlowState);
      const service = makeService();

      await service.handleEscalation(
        makeEvent({ id: 'a', type: 'deadline', payload: { overdue: true, severity: 'critical' } }),
      );
      await service.handleEscalation(
        makeEvent({ id: 'b', type: 'job-completed', payload: { severity: 'info' } }),
      );

      expect(pushChannel.deliver).toHaveBeenCalledTimes(1);
      expect(digestChannel.deliver).toHaveBeenCalledTimes(1);
      expect(batchChannel.deliver).not.toHaveBeenCalled();
    });
  });

  describe('error handling', () => {
    it('does not throw when flow state lookup fails — falls back to available', async () => {
      mockGetFlowState.mockImplementation(() => {
        throw new Error('flow state unavailable');
      });
      const service = makeService();

      const event = makeEvent({
        type: 'deadline',
        payload: { overdue: true, severity: 'critical' },
      });

      // Should not reject; should fall back to 'available' and deliver via push.
      const result = await service.handleEscalation(event);
      expect(result.channel).toBe('push');
      expect(pushChannel.deliver).toHaveBeenCalledTimes(1);
    });
  });
});
