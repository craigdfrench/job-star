// src/channels/silent-log.ts
// Appends escalations to a log file with no user-facing surface.

import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import type { NotificationChannel, Escalation, DeliveryResult } from './base';
import { formatEscalation } from './base';

/**
 * SilentLog writes escalations to a file on disk. No stdout, no UI.
 * Intended for `silent` urgency — record-keeping without interruption.
 */
export class SilentLog implements NotificationChannel {
  readonly name = 'silent';

  constructor(
    private readonly filePath: string = '.jobstar/silent.log',
    /** Injectable appender for testing. */
    private readonly appender: (path: string, line: string) => void = defaultAppender,
  ) {}

  deliver(e: Escalation): DeliveryResult {
    try {
      const line = `${formatEscalation(e)}\n`;
      this.appender(this.filePath, line);
      return { channel: this.name, delivered: true, at: Date.now() };
    } catch (err) {
      return {
        channel: this.name,
        delivered: false,
        error: err instanceof Error ? err.message : String(err),
        at: Date.now(),
      };
    }
  }
}

function defaultAppender(path: string, line: string): void {
  mkdirSync(dirname(path), { recursive: true });
  appendFileSync(path, line, 'utf8');
}
