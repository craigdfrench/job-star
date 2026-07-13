#!/usr/bin/env tsx
/**
 * Job-Star v0.0.1 — The Seed CLI
 *
 * This is the bootstrap. The simplest thing that can start the loop:
 *   - Add goals to a Postgres goal registry
 *   - List and show goals
 *   - Trigger AI to work on goals
 *
 * Every component of job-star will eventually be built using job-star itself.
 * This file is the assembly compiler. Everything else is compiled by it.
 *
 * Usage:
 *   job-star add "title" [--domain coding] [--urgency soon]
 *   job-star list
 *   job-star show <id>
 *   job-star step <id> "step title"
 *   job-star work <id>
 *   job-star complete <id>
 *   job-star digest [N]
 *   job-star help
 */

import { cmdAdd, cmdList, cmdShow, cmdStep, cmdWork, cmdComplete, cmdDigest, cmdHelp } from './commands/seed.ts';
import { closePool } from './db.ts';

async function main(): Promise<void> {
  const [command, ...args] = process.argv.slice(2);

  if (!command || command === 'help' || command === '--help' || command === '-h') {
    cmdHelp();
    return;
  }

  try {
    switch (command) {
      case 'add':
        await cmdAdd(args);
        break;
      case 'list':
        await cmdList(args);
        break;
      case 'ls':
        await cmdList(args);
        break;
      case 'show':
        await cmdShow(args);
        break;
      case 'step':
        await cmdStep(args);
        break;
      case 'work':
        await cmdWork(args);
        break;
      case 'complete':
        await cmdComplete(args);
        break;
      case 'digest':
        await cmdDigest(args);
        break;
      default:
        console.error(`Unknown command: ${command}`);
        console.error(`Run 'job-star help' for usage.`);
        process.exit(1);
    }
  } catch (err) {
    if (err instanceof Error) {
      console.error(`Error: ${err.message}`);
    } else {
      console.error('An unexpected error occurred:', err);
    }
    process.exit(1);
  } finally {
    await closePool();
  }
}

main();