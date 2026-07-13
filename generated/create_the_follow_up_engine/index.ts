{
  "$schema": "../src/config/schema.json",
  "version": 1,
  "dnd": {
    "enabled": false,
    "start": "22:00",
    "end": "07:00",
    "allowInterruptOverride": true
  },
  "flow": {
    "idleMinutes": 5,
    "focusSessionMinutes": 15,
    "cooldownMinutes": 2,
    "signalStaleSeconds": 120
  },
  "batch": {
    "flushIntervalSeconds": 900,
    "maxBatchSize": 8,
    "flushOnIdle": true
  },
  "urgency": {
    "overrides": [
      {
        "match": { "source": "deadline", "label": "offer-response" },
        "level": "interrupt"
      },
      {
        "match": { "source": "stale-thread", "daysOld": 7 },
        "level": "silent"
      }
    ]
  },
  "channels": {
    "preferred": ["desktop", "push", "email"],
    "disabled": [],
    "quietDesktopDuringFocus": true
  },
  "logging": {
    "level": "info"
  }
}


// --- DUPLICATE BLOCK ---

/**
 * Job-Star · Configuration loader
 *
 * Loads `config/job-star.json` (if present), deep-merges it over built-in
 * defaults, validates the shape, and exposes a typed singleton.
 *
 * Defaults are sourced from the existing modules:
 *   - src/config/urgency-rules.ts  (default urgency rules)
 *   - src/config/flow-thresholds.ts (default flow thresholds)
 *
 * Partial user configs are fine — anything omitted falls back to defaults.
 */

import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import * as path from 'node:path';

import { DEFAULT_URGENCY_RULES } from './urgency-rules.js';
import { DEFAULT_FLOW_THRESHOLDS } from './flow-thresholds.js';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type UrgencyLevel = 'interrupt' | 'batch' | 'silent';
export type ChannelName = 'desktop' | 'push' | 'email' | 'sms' | 'webhook';
export type FlowState = 'focus' | 'active' | 'idle' | 'offline';

export interface UrgencyOverride {
  /** Partial match criteria — all present fields must match the escalation. */
  match: {
    source?: string;
    label?: string;
    daysOld?: number;
    priority?: number;
    [k: string]: unknown;
  };
  level: UrgencyLevel;
}

export interface DndConfig {
  enabled: boolean;
  /** 24h "HH:MM" local time. */
  start: string;
  end: string;
  /** If true, interrupt-level escalations still surface during DND. */
  allowInterruptOverride: boolean;
}

export interface FlowConfig {
  idleMinutes: number;
  focusSessionMinutes: number;
  cooldownMinutes: number;
  signalStaleSeconds: number;
}

export interface BatchConfig {
  flushIntervalSeconds: number;
  maxBatchSize: number;
  /** Flush the batch immediately when user goes idle. */
  flushOnIdle: boolean;
}

export interface ChannelsConfig {
  /** Ordered preference — first available wins. */
  preferred: ChannelName[];
  disabled: ChannelName[];
  /** Suppress desktop notifications while in focus state. */
  quietDesktopDuringFocus: boolean;
}

export interface LoggingConfig {
  level: 'debug' | 'info' | 'warn' | 'error';
}

export interface JobStarConfig {
  version: number;
  dnd: DndConfig;
  flow: FlowConfig;
  batch: BatchConfig;
  urgency: {
    overrides: UrgencyOverride[];
  };
  channels: ChannelsConfig;
  logging: LoggingConfig;
}

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

const DEFAULT_CONFIG: JobStarConfig = {
  version: 1,
  dnd: {
    enabled: false,
    start: '22:00',
    end: '07:00',
    allowInterruptOverride: true,
  },
  flow: {
    // Mirror DEFAULT_FLOW_THRESHOLDS so the config is self-describing even
    // before any JSON file exists.
    idleMinutes: DEFAULT_FLOW_THRESHOLDS.idleMinutes ?? 5,
    focusSessionMinutes: DEFAULT_FLOW_THRESHOLDS.focusSessionMinutes ?? 15,
    cooldownMinutes: DEFAULT_FLOW_THRESHOLDS.cooldownMinutes ?? 2,
    signalStaleSeconds: DEFAULT_FLOW_THRESHOLDS.signalStaleSeconds ?? 120,
  },
  batch: {
    flushIntervalSeconds: 900,
    maxBatchSize: 8,
    flushOnIdle: true,
  },
  urgency: {
    overrides: [],
  },
  channels: {
    preferred: ['desktop', 'push', 'email'],
    disabled: [],
    quietDesktopDuringFocus: true,
  },
  logging: {
    level: 'info',
  },
};

// ---------------------------------------------------------------------------
// Deep merge
// ---------------------------------------------------------------------------

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/**
 * Deep-merge `override` onto `base`. Arrays are replaced, not concatenated,
 * so user config arrays are authoritative (predictable for overrides/lists).
 */
function deepMerge<T>(base: T, override: unknown): T {
  if (!isPlainObject(override)) {
    return (override as T) ?? base;
  }
  if (!isPlainObject(base)) {
    return override as T;
  }
  const out: Record<string, unknown> = { ...(base as Record<string, unknown>) };
  for (const [key, val] of Object.entries(override)) {
    if (isPlainObject(val) && isPlainObject(out[key])) {
      out[key] = deepMerge(out[key], val);
    } else if (val !== undefined) {
      out[key] = val;
    }
  }
  return out as T;
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

function assertString(v: unknown, field: string): void {
  if (typeof v !== 'string') throw new ConfigError(`${field} must be a string`);
}

function assertNumber(v: unknown, field: string, min = 0): void {
  if (typeof v !== 'number' || Number.isNaN(v) || v < min) {
    throw new ConfigError(`${field} must be a number >= ${min}`);
  }
}

function assertTime(v: unknown, field: string): void {
  assertString(v, field);
  if (typeof v === 'string' && !/^\d{2}:\d{2}$/.test(v)) {
    throw new ConfigError(`${field} must be "HH:MM" (got "${v}")`);
  }
}

export class ConfigError extends Error {
  constructor(msg: string) {
    super(`[Job-Star config] ${msg}`);
    this.name = 'ConfigError';
  }
}

function validate(cfg: JobStarConfig): void {
  assertNumber(cfg.version, 'version', 1);
  assertTime(cfg.dnd.start, 'dnd.start');
  assertTime(cfg.dnd.end, 'dnd.end');
  assertNumber(cfg.flow.idleMinutes, 'flow.idleMinutes');
  assertNumber(cfg.flow.focusSessionMinutes, 'flow.focusSessionMinutes');
  assertNumber(cfg.flow.cooldownMinutes, 'flow.cooldownMinutes');
  assertNumber(cfg.flow.signalStaleSeconds, 'flow.signalStaleSeconds');
  assertNumber(cfg.batch.flushIntervalSeconds, 'batch.flushIntervalSeconds');
  assertNumber(cfg.batch.maxBatchSize, 'batch.maxBatchSize', 1);

  if (cfg.channels.preferred.length === 0) {
    throw new ConfigError('channels.preferred must not be empty');
  }
  for (const ch of cfg.channels.preferred) {
    if (!ALLOWED_CHANNELS.has(ch)) {
      throw new ConfigError(`Unknown channel "${ch}" in channels.preferred`);
    }
  }
  for (const ch of cfg.channels.disabled) {
    if (!ALLOWED_CHANNELS.has(ch)) {
      throw new ConfigError(`Unknown channel "${ch}" in channels.disabled`);
    }
  }

  cfg.urgency.overrides.forEach((o, i) => {
    if (!['interrupt', 'batch', 'silent'].includes(o.level)) {
      throw new ConfigError(`urgency.overrides[${i}].level invalid: "${o.level}"`);
    }
    if (!isPlainObject(o.match)) {
      throw new ConfigError(`urgency.overrides[${i}].match must be an object`);
    }
  });
}

const ALLOWED_CHANNELS = new Set<ChannelName>([
  'desktop', 'push', 'email', 'sms', 'webhook',
]);

// ---------------------------------------------------------------------------
// Loader
// ---------------------------------------------------------------------------

const CONFIG_PATHS = [
  process.env.JOBSTAR_CONFIG ?? '',
  path.resolve(process.cwd(), 'config/job-star.json'),
  path.resolve(process.cwd(), 'job-star.json'),
].filter(Boolean);

let _cached: JobStarConfig | null = null;

/**
 * Locate and parse the user config file. Returns `null` if none found.
 */
function readUserConfig(): Record<string, unknown> | null {
  for (const p of CONFIG_PATHS) {
    if (p && existsSync(p)) {
      try {
        const raw = readFileSync(p, 'utf8');
        return JSON.parse(raw) as Record<string, unknown>;
      } catch (err) {
        throw new ConfigError(`Failed to read config at ${p}: ${(err as Error).message}`);
      }
    }
  }
  return null;
}

/**
 * Load (or return cached) merged configuration.
 *
 * Pass `{ reload: true }` to bypass cache — useful in tests or after a
 * config-file hot-reload.
 */
export function loadConfig(opts: { reload?: boolean } = {}): JobStarConfig {
  if (_cached && !opts.reload) return _cached;

  const userCfg = readUserConfig();
  const merged = userCfg
    ? deepMerge(DEFAULT_CONFIG, userCfg)
    : DEFAULT_CONFIG;

  validate(merged);
  _cached = merged;
  return merged;
}

/**
 * Convenience: get the effective flow thresholds in the shape the
 * FlowStateTracker expects, sourced from the loaded config.
 */
export function getFlowThresholds() {
  const cfg = loadConfig();
  return {
    idleMinutes: cfg.flow.idleMinutes,
    focusSessionMinutes: cfg.flow.focusSessionMinutes,
    cooldownMinutes: cfg.flow.cooldownMinutes,
    signalStaleSeconds: cfg.flow.signalStaleSeconds,
  };
}

/**
 * Convenience: get the effective urgency rules = defaults + user overrides.
 * The classifier should consult overrides first (they win).
 */
export function getUrgencyRules() {
  const cfg = loadConfig();
  return {
    defaults: DEFAULT_URGENCY_RULES,
    overrides: cfg.urgency.overrides,
  };
}

/** Reset cache — primarily for tests. */
export function _resetConfigCache(): void {
  _cached = null;
}

// Default export: the loaded config, evaluated at import time.
export default loadConfig();
