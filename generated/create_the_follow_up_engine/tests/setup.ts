// tests/setup.ts
// Shared setup for all test files. Currently minimal — reserved for
// global mocks, fake timers, or env var defaults as the suite grows.
import { vi } from 'vitest';

// If any test needs deterministic time, it can use vi.useFakeTimers()
// locally. We don't enable it globally to avoid surprising date logic.
export {};
