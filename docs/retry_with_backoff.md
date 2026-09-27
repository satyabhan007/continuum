# Micro-Design: Retry-with-Backoff Helper

Status: proposed
Scope: wrap real harness calls (`adapter.run(...)`) with bounded, jittered retries driven by the existing `execution_settings` config knobs.
Deliverable type: design only. No production code changes are required by this doc.

## Context (30 seconds)

`execution_settings` in `multi_harness_config.json` already defines `retry_attempts: 3` and `backoff_factor: 1.5`, but nothing reads them today. Real harness execution goes through one unprotected call site:

```python
# multi_harness_orchestrator.py :: _execute_step_with_agent (line 404 today)
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
    deadline: Optional[float] = 300.0 # budget for the WHOLE loop, measured from loop start
    jitter: bool = True                # equal jitter, see timing rules

    @classmethod
    def from_config(cls, s: dict) -> "RetryPolicy":
        """execution_settings -> policy. Existing keys stay the source of truth."""
        deadline = float(s.get("max_execution_time_seconds", 300.0) or 0)
        # NOTE: deadline is a RELATIVE budget (measured from loop entry),
        # never an absolute monotonic timestamp - see R4.
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
    if result is None and exc is None:
        return True   # fn returned None: contract violation, treat as transient (FM11)
    if result is not None and not result.ok:
        err = (getattr(result, "error", "") or "").lower()
        if ("not installed" in err or "not configured" in err
                or "not authenticated" in err or "credentials" in err
                or "no headless" in err):
            return False
        return True   # rc != 0, non-JSON output, empty text, timeouts
    return exc is not None   # defensive: adapters promise never to raise


async def retry_with_backoff(
    fn: Callable[[], Awaitable[Any]],
    policy: RetryPolicy,
    is_retryable: Callable[[Any, Optional[BaseException]], bool] = default_is_retryable,
    on_attempt: Optional[Callable[[int, Any, Optional[BaseException]], None]] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], float] = time.monotonic,
) -> Any:
    """Call fn() up to policy.attempts times with exponential backoff.

    fn must return an object with an `ok` attribute (AgentRunResult for
    adapters). If it returns None, the attempt is treated as a transient
    failure and, if it persists to the last attempt, a synthesized
    AgentRunResult(ok=False, error="adapter returned None") is returned -
    the caller NEVER receives None (FM11).

    on_attempt(attempt_no, result, exc) fires after EVERY attempt,
    including ok=False ones, so callers can count tokens spent on failed
    attempts (FM6). The return value alone cannot do this: it carries
    only the LAST attempt. A cancelled attempt never fires it (FM12).

    deadline is a budget measured from loop entry (start = now() once,
    then (now() - start) + sleep_k > deadline stops the loop), never an
    absolute monotonic timestamp (R4).

    Ordinary failures never raise: the last result is returned. Three
    exceptions propagate instead:
      1. NonRetryableError from ANY attempt, immediately - fail-fast is
         its entire purpose.
      2. An unexpected exception from the last completed attempt when
         the loop can proceed no further - attempts exhausted, a custom
         is_retryable veto, or a deadline stop (R4). Fabricating ok=False
         here would mask programming bugs.
      3. Cancellation - asyncio.CancelledError / KeyboardInterrupt -
         immediately from the attempt that raised it. Never classified,
         never retried, never converted to ok=False (FM12).
    """
```

The only orchestrator change this doc implies is a one-line wrap at the call site:

```python
policy = RetryPolicy.from_config(self.config["execution_settings"])

spent = {"tokens": 0}
def _count(attempt_no, result, exc):          # FM6: budget pays for FAILED
    spent["tokens"] += getattr(result, "tokens_used", 0) or 0  # attempts too

result = await retry_with_backoff(
    lambda: adapter.run(prompt, workdir=os.getcwd()),
    policy=policy,
    on_attempt=_count,
)
if not result.ok:
    raise HarnessError(f"agent {adapter.name}: {result.error[:200]}")
# budget accounting switches to spent["tokens"] (covers failures);
# result.tokens_used still records the winning attempt for the step outcome
# ... downstream stays unchanged: baton history, step records
```

**Design properties, and why:**

- **Returns a result, never raises for ordinary failures.** Matches the `BaseAdapter.run` contract ("Must never raise - returns ok=False"), so the relay loop and handoff path need zero new error handling.
- **`attempts` counts total tries.** `retry_attempts: 3` therefore means 1 initial call + 2 retries. This must be pinned in the doc because the key name is ambiguous; total-attempts bounds worst-case latency tighter than "3 retries after the first try".
- **`sleep` and `now` are injected.** Tests run in microseconds with a fake clock; production uses `asyncio.sleep` (event-loop friendly, see FM7) and `time.monotonic` (immune to wall-clock jumps).
- **`is_retryable` is a parameter.** Per-harness policies (e.g., never retry `cline`'s stub) are possible without touching the loop.
- **`on_attempt` is the FM6 hook.** Failed attempts burn tokens too, and the return value only carries the last attempt's `tokens_used`; the caller aggregates spend across attempts via this callback (validation showed a 3-attempt run spending 1000 tokens would otherwise be booked as 200).
- **`fn` returning `None` is a failed attempt, not a crash.** A contract-violating adapter yields a synthesized `AgentRunResult(ok=False)`; `None` never leaks to the caller's `result.ok` access. The classifier itself also treats a bare `None` return (no exception) as transient, so the behavior does not depend on whether the implementation synthesizes the placeholder before or after classification. The helper imports `AgentRunResult` from `harness_adapters` (no import cycle).
- **`NonRetryableError` propagates immediately from any attempt.** Fail-fast is the exception's purpose; swallowing it until the final attempt would contradict its name (escape hatch 1).
- **Cancellation is exempt from the never-raise contract.** The helper catches `Exception`, not `BaseException`, so `asyncio.CancelledError` and `KeyboardInterrupt` escape the loop the instant `fn` raises them (escape hatch 3, FM12). Retrying a cancellation fights the event loop's shutdown; converting it to `ok=False` would mask a deliberate abort as a harness failure.
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

**R4 - One RELATIVE deadline for the whole loop.** `deadline` (from `max_execution_time_seconds`, default 300s) is a budget measured from loop entry, not an absolute timestamp. The helper captures `start = now()` exactly once, then checks **before each sleep**: if `(now() - start) + sleep_k` exceeds `deadline`, stop instead of sleeping past the budget. The comparison MUST be start-relative: `time.monotonic()` counts from machine boot (observed on this machine: ~132,716s), so an absolute check `now() + sleep_k > deadline` is always true on any machine up longer than 300s and silently suppresses every retry - the helper degrades to a single attempt and nothing reports it. Validation of the original absolute wording caught exactly this failure. Two deliberate corollaries:

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
| FM3 | Deadline exceeded with attempts remaining | Stop before sleeping past the budget, measured from loop start (R4); log `deadline_exceeded` | Respect `max_execution_time_seconds`; one stuck harness must not hold the relay hostage |
| FM4 | Retry amplification across layers | Retries are per-harness-per-step only, never nested with the recovery loop. Worst case for one stuck step: 3 attempts x `max_recovery_attempts: 5` x 4 harnesses = 60 runs, but `max_delay` caps every wait | Two independent bounded layers compose better than one unbounded one |
| FM5 | Non-idempotent partial work | A timed-out agent may have already edited files; the re-prompt is sent verbatim, so work can be duplicated. Accept + log | Baton history (`completed_steps`) is only refreshed across handoffs, not within a retry loop; true idempotency keys are explicitly out of scope |
| FM6 | Token spend on failed attempts | Failed runs can burn tokens without producing text; retries multiply spend. The return value only carries the LAST attempt, so the caller MUST aggregate via the `on_attempt` callback: add `tokens_used` from **every** attempt (even `ok=False`) into the budget | Without this, retries silently bypass `TokenBudget` accounting; validation showed a 3-attempt run spending 1000 tokens would be booked as 200 |
| FM7 | Event-loop blocking | Never `time.sleep`; only the injected async sleep | Keeps `TokenMonitor` and parallel harness tasks live during waits |
| FM8 | Config drift / missing keys | Unknown or absent keys fall back to code defaults (3 / 1.5 / 300) | `load_config` already merges file config over defaults; the helper inherits that tolerance |
| FM9 | "Empty text" false negative | `ok=bool(text)` in adapters means a legitimately empty rc=0 run is retryable and eventually fails | Acceptable: a relay step must produce a baton block; empty output IS a failure by definition |
| FM10 | Classifier misjudges | A wrong `is_retryable` decision wastes one retry (or skips one) but never crashes or hangs | Classifier is injectable per call site; the substring list in `default_is_retryable` is the single place to tune |
| FM11 | `fn` returns `None` (contract-violating adapter) | Treated as a transient failed attempt; if it persists to the last attempt, return a synthesized `AgentRunResult(ok=False, error="adapter returned None")` | "Never crash" must also cover broken adapters: a `None` leaking to the caller explodes at `result.ok` (AttributeError) - exactly what the relay must not do |
| FM12 | Cancellation (`asyncio.CancelledError`, `KeyboardInterrupt`) while `fn` runs | Propagates immediately from the raising attempt; never classified, never retried, never converted to `ok=False`, never reported to `on_attempt` | The loop catches `Exception`, not `BaseException`, so both escape naturally. A retried cancellation fights the event loop's shutdown; a fabricated `ok=False` would mask a deliberate abort as a harness failure - and burn up to `attempts-1` extra runs after the caller already asked to stop |

**The three contract escape hatches:** (1) `NonRetryableError` raised by `fn` propagates immediately from ANY attempt, not just the final one - fail-fast is the exception's entire purpose, and swallowing it would contradict its name. (2) If the last completed attempt raised an unexpected exception and the loop can proceed no further - attempts exhausted, a custom `is_retryable` veto, or a deadline stop (R4) - the helper re-raises that exception rather than fabricating a result. (Impossible for today's adapters, which convert exceptions to `ok=False` results, but possible for buggy future ones; if a later attempt succeeds instead, the earlier exception is observable only through `on_attempt`'s `exc` argument.) (3) Cancellation escapes immediately: the helper catches `Exception`, not `BaseException`, so `asyncio.CancelledError` and `KeyboardInterrupt` propagate the instant `fn` raises them (FM12). Ordinary failures never raise; programming bugs and deliberate aborts should not be silently swallowed. A `None` return is NOT an escape hatch - it is FM11 and degrades to a failed result.

---

## Appendix - Integration notes (not part of the 3 core sections)

- **Files touched if implemented:** new `retry_backoff.py`; one-line wrap at `multi_harness_orchestrator.py:404`; optional config keys in `multi_harness_config.json`.
- **Test plan (no real sleeping, via injected `sleep`/`now`):**
  1. Success on first try -> 0 sleeps, 1 call.
  2. Fail, fail, succeed -> 2 calls to sleep, delays within `[0.5, 1.0]` and `[0.75, 1.5]`.
  3. All fail -> 3 calls, last failed result returned, no exception.
  4. Unavailable adapter (`check()` false) -> 1 call, 0 sleeps (FM2).
  5. Deadline hit mid-loop -> stops early even with attempts remaining (FM3).
  6. `attempts: 1` and `backoff_factor: 0.5` -> clamped, single attempt, no crash (R5).
  7. Deadline with the REAL `time.monotonic` injected as `now` -> all 3 attempts still run, because the budget is measured from loop start (R4). Guards the absolute-vs-relative bug found in validation: with the absolute reading, a machine up >300s silently ran 1 attempt.
  8. `fn` returns `None` every time -> helper returns `ok=False` with error `adapter returned None`, 3 attempts made, no exception, caller can safely access `result.ok` (FM11).
  9. `fn` raises `NonRetryableError` on attempt 2 of 3 -> propagates immediately after that attempt; `on_attempt` observed both attempts (the second with `exc=NonRetryableError`) and no retry follows it (escape hatch 1).
  10. Token aggregation: attempts spending 500/300/200 tokens -> `on_attempt` total = 1000 even though the returned result reports `tokens_used=200` (FM6).
  11. `fn` raises `asyncio.CancelledError` on attempt 2 of 3 -> propagates immediately: no sleep, no third attempt, no fabricated result (FM12, escape hatch 3).
  12. Custom `is_retryable` returns False for an unexpected exception on attempt 2 of 3 -> that exception propagates rather than a fabricated result (escape hatch 2, non-final case).
  13. Unexpected exception on attempts 1-2, success on attempt 3 -> the helper returns the successful result; the two exceptions were observable only via `on_attempt` (escape hatch 2's "later attempt succeeds" clause).
