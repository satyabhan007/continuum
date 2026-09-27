# Micro-Design: Retry-with-Backoff Helper

Status: proposed
Scope: wrap real harness calls (`adapter.run(...)`) with bounded, jittered retries driven by the existing `execution_settings` config knobs.
Deliverable type: design only. No production code changes are required by this doc.

## Context (30 seconds)

`execution_settings` in `multi_harness_config.json` already defines `retry_attempts: 3` and `backoff_factor: 1.5`, but nothing reads them today. Real harness execution goes through one unprotected call site:

```python
# multi_harness_orchestrator.py :: _execute_step_with_agent (line 358 today)
result = await adapter.run(prompt, workdir=os.getcwd())   # one shot
if not result.ok:
    raise HarnessError(...)                               # straight to handoff
```

Any transient hiccup (CLI hiccups, non-JSON output, empty response) immediately burns the whole attempt and forces a cross-harness handoff, which is far more expensive than a short in-place retry. The helper below adds the cheap middle tier between "first try" and "full handoff", while honoring the project's core rule: **NEVER crash, always degrade gracefully** (`harness_adapters.py`).

---

## Step 1 - The API sketch

A new module, `retry_backoff.py`, with three public pieces: a `RetryPolicy`, a fail-fast exception, and one function.

```python
"""retry_backoff.py - retry-with-backoff helper (micro-design sketch)."""

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional


@dataclass
class RetryPolicy:
    attempts: int = 3                  # TOTAL tries: initial + (attempts-1) retries
    backoff_factor: float = 1.5
    base_delay: float = 1.0            # seconds, wait before the first retry
    max_delay: float = 30.0            # cap on any single wait
    deadline: Optional[float] = 300.0 # wall-clock budget for the whole loop
    jitter: bool = True                # equal jitter, see timing rules

    @classmethod
    def from_config(cls, s: dict) -> "RetryPolicy":
        """execution_settings -> policy. Existing keys stay the source of truth."""
        deadline = float(s.get("max_execution_time_seconds", 300.0) or 0)
        return cls(
            attempts=max(1, int(s.get("retry_attempts", 3))),
            backoff_factor=max(1.0, float(s.get("backoff_factor", 1.5))),
            base_delay=max(0.0, float(s.get("retry_base_delay", 1.0))),
            max_delay=max(0.0, float(s.get("retry_max_delay", 30.0))),
            deadline=deadline or None,
            jitter=bool(s.get("retry_jitter", True)),
        )


class NonRetryableError(Exception):
    """fn raises this when retrying cannot possibly help."""


def default_is_retryable(result: Any, exc: Optional[BaseException]) -> bool:
    """Fail fast on harness unavailability; treat everything else as transient.

    Grounded in the exact error strings harness_adapters.py produces.
    """
    if isinstance(exc, NonRetryableError):
        return False
    if result is not None and not result.ok:
        err = (getattr(result, "error", "") or "").lower()
        if ("not installed" in err or "not configured" in err
                or "no headless" in err):
            return False
        return True   # rc != 0, non-JSON output, empty text, timeouts
    return exc is not None   # defensive: adapters promise never to raise


async def retry_with_backoff(
    fn: Callable[[], Awaitable[Any]],
    policy: RetryPolicy,
    is_retryable: Callable[[Any, Optional[BaseException]], bool] = default_is_retryable,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], float] = time.monotonic,
) -> Any:
    """Call fn() up to policy.attempts times with exponential backoff.

    Never raises for ordinary failures (they arrive as ok=False results).
    Re-raises only if the final attempt itself threw, so bugs are not masked.
    Returns the last result object (an AgentRunResult for real adapters).
    """
```

The only orchestrator change this doc implies is a one-line wrap at the call site:

```python
policy = RetryPolicy.from_config(self.config["execution_settings"])
result = await retry_with_backoff(
    lambda: adapter.run(prompt, workdir=os.getcwd()),
    policy=policy,
)
if not result.ok:
    raise HarnessError(f"agent {adapter.name}: {result.error[:200]}")
# ... downstream stays unchanged: baton history, real token accounting
```

**Design properties, and why:**

- **Returns a result, never raises for ordinary failures.** Matches the `BaseAdapter.run` contract ("Must never raise - returns ok=False"), so the relay loop and handoff path need zero new error handling.
- **`attempts` counts total tries.** `retry_attempts: 3` therefore means 1 initial call + 2 retries. This must be pinned in the doc because the key name is ambiguous; total-attempts bounds worst-case latency tighter than "3 retries after the first try".
- **`sleep` and `now` are injected.** Tests run in microseconds with a fake clock; production uses `asyncio.sleep` (event-loop friendly, see FM7) and `time.monotonic` (immune to wall-clock jumps).
- **`is_retryable` is a parameter.** Per-harness policies (e.g., never retry `cline`'s stub) are possible without touching the loop.
- **No new mandatory config.** `retry_attempts`, `backoff_factor`, and `max_execution_time_seconds` keep their current names and values; `retry_base_delay`, `retry_max_delay`, `retry_jitter` are optional additions with code defaults.

---

## Step 2 - Backoff timing rules

**R1 - Exponential base schedule.** Wait before retry `k` (1-based) is:

```
w_k = min(max_delay, base_delay * backoff_factor ** (k - 1))
```

**R2 - Equal jitter (when `jitter=True`).** The actual sleep is `w_k / 2 + U(0, w_k / 2)`, i.e. uniform in `[w_k/2, w_k]`. This keeps a deterministic floor of half the computed wait (predictable behavior, backoff still respected) while decorrelating retries from parallel harness tasks (`max_concurrent_tasks: 2`), preventing a thundering herd against a shared CLI.

**R3 - Default schedule** (`attempts=3`, `factor=1.5`, `base_delay=1.0`):

| after attempt | computed wait `w_k` | jittered sleep range | note |
|---------------|---------------------|----------------------|------|
| 1st fails     | 1.0 * 1.5^0 = 1.00s | [0.50, 1.00]s        | before retry 1 |
| 2nd fails     | 1.0 * 1.5^1 = 1.50s | [0.75, 1.50]s        | before retry 2 |
| 3rd fails     | none                | none                 | attempts exhausted -> FM1 |

Worst-case added latency with defaults: **2.50s** of sleeping across 3 attempts (1.25s minimum with jitter).

**R4 - One wall-clock deadline for the whole loop.** `deadline` (from `max_execution_time_seconds`, default 300s) covers all attempts plus sleeps, and is checked **before each sleep**: if `now() + sleep_k` would pass the deadline, stop retrying early instead of sleeping into the deadline. Two deliberate corollaries:

- At least one attempt always runs; the deadline can never suppress the first try.
- A single attempt is NOT pre-emptied by this helper. Single-attempt bounds come from the adapter's own `asyncio.wait_for(..., timeout=self.timeout)` (`harness_adapters.py`).

**R5 - Validation and clamping** (in `from_config`): `attempts < 1` -> 1; `backoff_factor <= 1` -> treated as 1.0 (constant delay, still valid); negative delays -> 0; `max_execution_time_seconds` falsy -> `None` (no deadline).

**R6 - Zero-delay fast path.** If the computed delay is 0 (e.g., `base_delay: 0`), the sleep call is skipped entirely, making the helper a pure "N attempts in a row" loop.

**R7 - Clock discipline.** Jitter never changes the attempt count, only wait lengths. All timing uses the monotonic clock, so NTP adjustments or laptop sleeps cannot corrupt deadline math.

---

## Step 3 - Failure modes

| # | Failure mode | Helper behavior | Rationale |
|---|--------------|------------------|-----------|
| FM1 | All attempts exhausted | Return the **last** `AgentRunResult` (`ok=False`); no exception. Caller raises `HarnessError` -> relay hands off to the next harness | Cheap tier first, expensive tier (cross-harness handoff) unchanged |
| FM2 | Non-retryable failure: CLI missing, OAuth not configured, no headless CLI (`cline`) | Fail fast: 1 attempt, 0 sleeps | Waiting cannot install software or finish an OAuth dance; error strings are the only coupling to `harness_adapters.py` |
| FM3 | Deadline exceeded with attempts remaining | Stop before sleeping past the budget; log `deadline_exceeded` | Respect `max_execution_time_seconds`; one stuck harness must not hold the relay hostage |
| FM4 | Retry amplification across layers | Retries are per-harness-per-step only, never nested with the recovery loop. Worst case for one stuck step: 3 attempts x `max_recovery_attempts: 5` x 4 harnesses = 60 runs, but `max_delay` caps every wait | Two independent bounded layers compose better than one unbounded one |
| FM5 | Non-idempotent partial work | A timed-out agent may have already edited files; the re-prompt is sent verbatim, so work can be duplicated. Accept + log | Baton history (`completed_steps`) is only refreshed across handoffs, not within a retry loop; true idempotency keys are explicitly out of scope |
| FM6 | Token spend on failed attempts | Failed runs can burn tokens without producing text; retries multiply spend. Count `tokens_used` from **every** attempt (even `ok=False`) into the budget | Without this, retries silently bypass `TokenBudget` accounting |
| FM7 | Event-loop blocking | Never `time.sleep`; only the injected async sleep | Keeps `TokenMonitor` and parallel harness tasks live during waits |
| FM8 | Config drift / missing keys | Unknown or absent keys fall back to code defaults (3 / 1.5 / 300) | `load_config` already merges file config over defaults; the helper inherits that tolerance |
| FM9 | "Empty text" false negative | `ok=bool(text)` in adapters means a legitimately empty rc=0 run is retryable and eventually fails | Acceptable: a relay step must produce a baton block; empty output IS a failure by definition |
| FM10 | Classifier misjudges | A wrong `is_retryable` decision wastes one retry (or skips one) but never crashes or hangs | Classifier is injectable per call site; the substring list in `default_is_retryable` is the single place to tune |

**The one contract escape hatch:** if the final attempt itself raises an exception (impossible for today's adapters, which convert exceptions to `ok=False` results, but possible for buggy future ones), the helper re-raises rather than fabricating a fake result. Ordinary failures never raise; programming bugs should not be silently swallowed.

---

## Appendix - Integration notes (not part of the 3 core sections)

- **Files touched if implemented:** new `retry_backoff.py`; one-line wrap at `multi_harness_orchestrator.py:358`; optional config keys in `multi_harness_config.json`.
- **Test plan (no real sleeping, via injected `sleep`/`now`):**
  1. Success on first try -> 0 sleeps, 1 call.
  2. Fail, fail, succeed -> 2 calls to sleep, delays within `[0.5, 1.0]` and `[0.75, 1.5]`.
  3. All fail -> 3 calls, last failed result returned, no exception.
  4. Unavailable adapter (`check()` false) -> 1 call, 0 sleeps (FM2).
  5. Deadline hit mid-loop -> stops early even with attempts remaining (FM3).
  6. `attempts: 1` and `backoff_factor: 0.5` -> clamped, single attempt, no crash (R5).
