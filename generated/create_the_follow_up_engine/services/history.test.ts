// src/services/history.test.ts
//
// Tests for HistoryService. Uses an isolated temp log file per test.

import * as os from 'os';
import * as path from 'path';
import * as fs from 'fs';
import { HistoryService, EscalationRecord } from './history';

function tmpLogPath(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jobstar-history-'));
  return path.join(dir, 'escalations.jsonl');
}

function baseRec(over: Partial<EscalationRecord> = {}): EscalationRecord {
  return {
    id: 'esc-1',
    source: 'github-watcher',
    summary: 'PR review requested',
    receivedAt: new Date('2025-01-01T00:00:00Z').toISOString(),
    status: 'pending',
    ...over,
  };
}

describe('HistoryService', () => {
  let logPath: string;
  let svc: HistoryService;

  beforeEach(() => {
    logPath = tmpLogPath();
    svc = new HistoryService(logPath);
  });

  afterEach(() => {
    const dir = path.dirname(logPath);
    if (fs.existsSync(dir)) fs.rmSync(dir, { recursive: true, force: true });
  });

  test('record() appends a JSONL line and creates the file', () => {
    svc.record(baseRec());
    const content = fs.readFileSync(logPath, 'utf8');
    const lines = content.trim().split('\n');
    expect(lines.length).toBe(1);
    expect(JSON.parse(lines[0]).id).toBe('esc-1');
  });

  test('multiple records append on separate lines', () => {
    svc.record(baseRec({ id: 'a' }));
    svc.record(baseRec({ id: 'b' }));
    const lines = fs.readFileSync(logPath, 'utf8').trim().split('\n');
    expect(lines.length).toBe(2);
  });

  test('getHistory filters by urgencyTier', () => {
    svc.record(baseRec({ id: '1', urgencyTier: 'interrupt', status: 'delivered' }));
    svc.record(baseRec({ id: '2', urgencyTier: 'batch', status: 'deferred' }));
    svc.record(baseRec({ id: '3', urgencyTier: 'silent', status: 'suppressed' }));

    const interrupts = svc.getHistory({ urgencyTier: 'interrupt' });
    expect(interrupts).toHaveLength(1);
    expect(interrupts[0].id).toBe('1');
  });

  test('getHistory filters by time window', () => {
    svc.record(baseRec({ id: 'old', receivedAt: '2025-01-01T00:00:00Z' }));
    svc.record(baseRec({ id: 'new', receivedAt: '2025-06-01T00:00:00Z' }));

    const recent = svc.getHistory({
      since: '2025-05-01T00:00:00Z',
      until: '2025-07-01T00:00:00Z',
    });
    expect(recent).toHaveLength(1);
    expect(recent[0].id).toBe('new');
  });

  test('getHistory respects limit', () => {
    for (let i = 0; i < 50; i++) {
      svc.record(baseRec({ id: `e-${i}` }));
    }
    expect(svc.getHistory({ limit: 5 })).toHaveLength(5);
    expect(svc.getHistory({ limit: 100 })).toHaveLength(50);
  });

  test('getStats aggregates counts', () => {
    svc.record(baseRec({ id: '1', urgencyTier: 'interrupt', channel: 'interrupt', status: 'delivered', source: 'gh' }));
    svc.record(baseRec({ id: '2', urgencyTier: 'batch', channel: 'batch', status: 'deferred', source: 'gh' }));
    svc.record(baseRec({ id: '3', urgencyTier: 'interrupt', channel: 'interrupt', status: 'failed', source: 'slack' }));

    const stats = svc.getStats();
    expect(stats.total).toBe(3);
    expect(stats.byUrgencyTier.interrupt).toBe(2);
    expect(stats.byUrgencyTier.batch).toBe(1);
    expect(stats.byChannel.interrupt).toBe(2);
    expect(stats.byStatus.delivered).toBe(1);
    expect(stats.byStatus.failed).toBe(1);
    expect(stats.bySource.gh).toBe(2);
    expect(stats.bySource.slack).toBe(1);
  });

  test('getLatestPerId collapses intake + outcome lines', () => {
    svc.record(baseRec({ id: 'X', status: 'pending' }));
    svc.record(baseRec({ id: 'X', status: 'delivered', urgencyTier: 'interrupt', channel: 'interrupt', deliveredAt: '2025-01-01T00:05:00Z' }));
    svc.record(baseRec({ id: 'Y', status: 'pending' }));

    const latest = svc.getLatestPerId();
    expect(latest).toHaveLength(2);
    const x = latest.find(r => r.id === 'X');
    expect(x?.status).toBe('delivered');
    expect(x?.urgencyTier).toBe('interrupt');
  });

  test('replay() only processes intake records and appends outcome lines', async () => {
    svc.record(baseRec({ id: 'A', status: 'pending' }));
    svc.record(baseRec({ id: 'B', status: 'pending' }));
    // Already-classified record should be skipped by replay
    svc.record(baseRec({ id: 'C', status: 'delivered', urgencyTier: 'interrupt' }));

    const n = await svc.replay({}, async (rec) => ({
      urgencyTier: 'batch' as const,
      channel: 'batch' as const,
      status: 'routed' as const,
      classificationReason: `replayed-from-${rec.id}`,
    }));

    expect(n).toBe(2); // A and B, not C
    const all = svc.getHistory({ limit: 1000 });
    expect(all.length).toBe(5); // 3 originals + 2 replay outcomes
    const replays = all.filter(r => r.source.startsWith('replay:'));
    expect(replays).toHaveLength(2);
    expect(replays.every(r => r.urgencyTier === 'batch')).toBe(true);
  });

  test('malformed lines are skipped, not fatal', () => {
    svc.record(baseRec({ id: 'good' }));
    fs.appendFileSync(logPath, '{not valid json\n', 'utf8');
    svc.record(baseRec({ id: 'good2' }));

    const results = svc.getHistory({ limit: 100 });
    expect(results.map(r => r.id)).toEqual(['good', 'good2']);
  });
});
