# Verification: Expert Field Assignment via Orchestrate & Submit

**Goal:** verify orchestrate sends expert field / verify expert assignment via submit
**Date:** 2026-08-15
**Status:** ✅ COMPLETE

## Summary

Verified end-to-end that the `expert` field is propagated correctly through
both the **orchestrate** pipeline and the **submit** assignment path. Both
paths were inspected, unit-tested, and integration-tested. All tests pass.

---

## 1. Orchestrate — expert field propagation

### Exact field name
- **`expert`** (string, optional) — the expert agent that owns the goal.
- Defaults to `null` (generic pool) when not provided.
- `null` / empty string / whitespace-only values are all treated as "no
  expert" and normalized to `null` before being sent downstream.

### Behavior observed
| Input | Normalized value sent downstream |
|-------|----------------------------------|
| `"gatehouse-ai"` | `"gatehouse-ai"` |
| `""` | `null` |
| `"   "` | `null` |
| (omitted) | `null` |

### Edge cases
- Expert values are **not** case-normalized — `"Gatehouse-AI"` is preserved
  as-is. Matching against worker affinity is case-sensitive downstream.
- The expert field is forwarded verbatim; orchestrate does not validate that
  the named expert exists in the worker registry.

---

## 2. Submit — expert assignment

### Exact field name
- **`expert`** (string, optional) on the submit payload.

### Behavior observed
- When `expert` is present and non-empty, the goal is created with that
  expert assigned.
- When `expert` is missing/empty, the goal falls back to the generic pool
  (`expert = null`).
- Assignment happens at goal-creation time; the value is persisted in the
  `goals.expert` column.

### Edge cases
- `expert: null` → generic pool.
- `expert: ""` → generic pool (normalized to null).
- Unknown expert names are accepted (no registry validation at submit time).

---

## 3. Tests

### Unit tests
- `src/orchestrate/__tests__/orchestrate.test.ts` — verifies the expert
  field is included in the orchestrate request and normalized correctly.
- `src/submit/__tests__/submit.test.ts` — verifies expert assignment at
  submit time, including null/empty fallback.

### Integration tests
- `tests/test_integration.py` — added coverage asserting the expert field
  round-trips through the intake → goal-creation path.

### Result