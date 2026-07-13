// src/channels/terminal.ts
// Immediate stdout delivery channel for interrupt-priority escalations.

import type { NotificationChannel, Escalation, DeliveryResult } from './base';
import { formatEscalation } from './base';

const ANSI = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  dim: '\x1b[2m',
} as const;

/**
 * TerminalChannel prints an escalation to stdout immediately with
 * urgency-aware coloring. Intended for `interrupt` urgency only —
 * using it for `silent` would defeat the purpose, but it won't refuse.
 */
export class TerminalChannel implements NotificationChannel {
  readonly name = 'terminal';

  constructor(
    /** Injectable writer so tests can capture output without spying on stdout. */
    private readonly write: (line: string) => void = (line) => process.stdout.write(line + '\n'),
  ) {}

  deliver(e: Escalation): DeliveryResult {
    try {
      const color = e.urgency === 'interrupt' ? ANSI.red : ANSI.yellow;
      const header = `${ANSI.bold}${color}⚠ Job-Star${ANSI.reset} ${ANSI.dim}(${e.urgency})${ANSI.reset}`;
      const body = `${ANSI.bold}${e.title}${ANSI.reset}\n${e.message}`;
      const meta = `${ANSI.dim}source: ${e.source} · id: ${e.id}${ANSI.reset}`;
      this.write(`${header}\n${body}\n${meta}\n`);
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
