# Micro-Design: Retry-with-Backoff Helper (scratch draft)

Status: scratch working draft. Long-form companion: `docs/retry_with_backoff.md`.
Deliverable type: design only. No production code changes required by this doc.

Grounded facts (verified in this workspace):
- `multi_harness_config.json` `execution_settings` (lines 70-76) defines `retry_attempts: 3`, `backoff_factor: 1.5`, `max_execution_time_seconds: 300`. Nothing drives retries with them today (`max_execution_time_seconds` is only read once, as a step-count heuristic at `multi_harness_orchestrator.py:171`).
- One unprotected call site: `multi_harness_orchestrator.py:385`, `result = await adapter.run(prompt, workdir=os.getcwd())` one shot, then `result.ok` false straight to `HarnessError` and a cross-harness handoff.
- Adapters promise `run()` never raises and always return `AgentRunResult(ok, text, tokens_used, provider, model, error)` (`harness_adapters.py:70-78`).
- Project rule: NEVER crash, always degrade gracefully. The helper adds the cheap middle tier between "first try" and "full handoff".

---

## Section 1 - API sketch

One new module, `retry_backoff.py`, with a policy dataclass, a fail-fast exception, and one function.

```python
@dataclass
class RetryPolicy:
    attempts: int = 3                    # TOTAL tries = initial + (attempts-1) retries
    backoff_factor: float = 1.5
    base_delay: float = 1.0              # seconds, wait before the FIRST retry
    max_delay: float = 30.0               # cap on any single wait
    deadline: Optional[float] = 300.0    # RELATIVE budget from loop entry (see R4)
    jitter: bool = True

    @classmethod
    def from_config(cls, s: dict) -> "RetryPolicy":
        """execution_settings -> policy. Existing config keys stay source of truth."""

class NonRetryableError(Exception):
    """fn raises this when retrying cannot possibly help."""

async def retry_with_backoff(
    fn: Callable[[], Awaitable[Any]],    # e.g. lambda: adapter.run(prompt, workdir=...)
    policy: RetryPolicy,
    is_retryable: Callable[[Any, Optional[BaseException]], bool] = default_is_retryable,
    on_attempt: Optional[Callable[[int, Any, Optional[BaseException]], None]] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,   # injected for tests
    now: Callable[[], float] = time.monotonic,
) -> Any:
    """Returns the LAST attempt's AgentRunResult. Ordinary failures never raise."""
```

**Contract:**
- Returns the last result, never raises for ordinary failures. Mirrors the `BaseAdapter.run` "must never raise, returns ok=False" contract, so the caller (orchestrator relay loop) needs zero new error handling.
- `attempts` counts TOTAL tries. `retry_attempts: 3` means 1 initial call + 2 retries. Pinned because the key name is ambiguous and total-attempts bounds worst-case latency tighter.
- `NonRetryableError` from any attempt propagates immediately (fail-fast is its entire purpose).
- Cancellation (`asyncio.CancelledError`, `KeyboardInterrupt`) propagates immediately. The loop catches `Exception`, not `BaseException`. Never classified, never retried, never converted to `ok=False`.
- `fn` returning `None` is a transient failed attempt. If it persists to the last attempt, return a synthesized `AgentRunResult(ok=False, error="adapter returned None")` so the caller never dereferences `result.ok` on `None`.
- `on_attempt(attempt_no, result, exc)` fires after EVERY attempt including failed ones. It is the only way to observe per-attempt token spend, because the return value carries only the last attempt (see FM6). A cancelled attempt never fires it.
- `default_is_retryable`: `False` for `not installed` / `not configured` / `no headless` error substrings (CLI missing, OAuth missing, cline's stub). `True` otherwise: nonzero rc, non-JSON output, empty text, timeouts. Error strings are the only coupling to `harness_adapters.py`.
- No new mandatory config. `retry_attempts`, `backoff_factor`, `max_execution_time_seconds` keep their names and values. `retry_base_delay`, `retry_max_delay`, `retry_jitter` are optional additions with code defaults.

**Call-site integration** (the only orchestrator change, one wrap at line 385):

```python
policy = RetryPolicy.from_config(self.config["execution_settings"])

spent = {"tokens": 0}
def _count(attempt_no, result, exc):     # FM6: failed attempts burn tokens too
    spent["tokens"] += getattr(result, "tokens_used", 0) or 0

result = await retry_with_backoff(
    lambda: adapter.run(prompt, workdir=os.getcwd()),
    policy=policy,
    on_attempt=_count,
)
if not result.ok:
    self._record_adapter_failure(adapter.name)
    raise HarnessError(f"agent {adapter.name}: {result.error[:200]}")
# budget accounting switches to spent["tokens"] (covers failed attempts)
```

---

## Section 2 - Backoff timing rules

**R1 - Exponential base schedule.** Wait before retry `k` (1-based):
```
w_k = min(max_delay, base_delay * backoff_factor ** (k - 1))
```

**R2 - Equal jitter** (`jitter=True`): actual sleep is uniform in `[w_k/2, w_k]`. Deterministic half-delay floor, decorrelated waits. Prevents a thundering herd against a shared CLI with `max_concurrent_tasks: 2`. Jitter never changes the attempt count, only wait lengths.

**R3 - Default schedule** (`attempts=3`, `factor=1.5`, `base_delay=1.0`):

| after attempt | computed wait | jittered range | note |
|---------------|---------------|----------------|------|
| 1st fails     | 1.0s          | [0.50, 1.00]s  | before retry 1 |
| 2nd fails     | 1.5s          | [0.75, 1.50]s  | before retry 2 |
| 3rd fails     | none          | none           | exhausted -> FM1 |

Worst-case added latency with defaults: **2.50s** of sleeping (1.25s minimum with jitter). Verified: 1.0 + 1.5 = 2.5.

**R4 - Deadline is a RELATIVE budget, not an absolute timestamp.** `start = now()` is captured exactly once at loop entry. Before each sleep, check `(now() - start) + w_k > deadline` and stop instead of sleeping past the budget. NEVER compare absolute values: `time.monotonic()` on this machine is ~134,313s (measured), so an absolute check like `now() + w_k > deadline` is always true on any machine up longer than 300s and silently suppresses every retry, degrading the helper to a single attempt with nothing reported. Corollaries: at least one attempt always runs, and a single attempt is not pre-emptied by this helper (per-attempt bounds come from the adapter's own `asyncio.wait_for(..., timeout=self.timeout)`).

**R5 - Clamping** (in `from_config`): `attempts < 1` -> 1; `backoff_factor <= 1` -> 1.0 (constant delay, still valid); negative delays -> 0; falsy `max_execution_time_seconds` -> `None` (no deadline).

**R6 - Zero-delay fast path.** If the computed delay is 0 (e.g. `base_delay: 0`), the sleep call is skipped entirely: pure "N attempts in a row".

**R7 - Clock discipline.** Monotonic clock only. NTP adjustments or laptop sleeps cannot corrupt deadline math.

---

## Section 3 - Failure modes

| # | Failure mode | Helper behavior | Rationale |
|---|--------------|------------------|-----------|
| FM1 | All attempts exhausted | Return the last `AgentRunResult` (`ok=False`), no exception. Caller raises `HarnessError` -> relay hands off | Cheap tier first, expensive tier (cross-harness handoff) unchanged |
| FM2 | Non-retryable: CLI missing, OAuth not configured, no headless CLI (`cline`) | Fail fast: 1 attempt, 0 sleeps | Waiting cannot install software or finish an OAuth dance |
| FM3 | Deadline exceeded with attempts remaining | Stop before sleeping past the budget (measured from loop entry, R4), log `deadline_exceeded` | One stuck harness must not hold the relay hostage |
| FM4 | Retry amplification across layers | Retries are per-harness-per-step only, never nested with the recovery loop. Worst case 3 attempts x `max_recovery_attempts: 5` x 4 harnesses = 60 runs, `max_delay` caps every wait | Two independent bounded layers compose better than one unbounded one |
| FM5 | Non-idempotent partial work | A timed-out agent may have already edited files, and the re-prompt is sent verbatim. Accept + log | Baton history refreshes only across handoffs, not within a retry loop |
| FM6 | Token spend on failed attempts | Return value carries only the LAST attempt's `tokens_used`. Caller MUST aggregate via `on_attempt`. Today `multi_harness_orchestrator.py:428` books `result.tokens_used or 500`, so a 3-attempt run spending 1000 tokens would be booked as 200 | Retries otherwise silently bypass `TokenBudget` accounting |
| FM7 | Event-loop blocking | Never `time.sleep`, only the injected async sleep | Keeps `TokenMonitor` and parallel harness tasks live during waits |
| FM8 | Config drift / missing keys | Unknown or absent keys fall back to code defaults (3 / 1.5 / 300) | `load_config` already merges file config over defaults |
| FM9 | "Empty text" false negative | `ok=bool(text)` in adapters means a legitimately empty rc=0 run is retryable and eventually fails | A relay step must produce a baton block, so empty output IS a failure |
| FM10 | Classifier misjudges | A wrong `is_retryable` decision wastes or skips one retry, never crashes or hangs | Classifier is injectable per call site, substring list is the single tuning point |
| FM11 | `fn` returns `None` (contract-violating adapter) | Treated as a transient failed attempt. If it persists, synthesized `AgentRunResult(ok=False, error="adapter returned None")` | "Never crash" must cover broken adapters too, since `None` leaking to the caller explodes at `result.ok` |
| FM12 | Cancellation while `fn` runs | `asyncio.CancelledError` / `KeyboardInterrupt` propagate immediately from the raising attempt. Never classified, never retried, never converted to `ok=False`, never reported to `on_attempt` | Retried cancellation fights the event loop's shutdown, and a fabricated failure masks a deliberate abort |

**Contract escape hatches** (ordinary failures never raise, these do):
1. `NonRetryableError` propagates immediately from ANY attempt.
2. An unexpected exception from the last completed attempt when the loop can proceed no further (attempts exhausted, custom `is_retryable` veto, or deadline stop) is re-raised, not fabricated into `ok=False`. If a later attempt succeeds instead, the earlier exception is observable only via `on_attempt`'s `exc`.
3. Cancellation escapes immediately (FM12).

---

## Appendix - test plan sketch (injected `sleep`/`now`, no real waiting)

1. First-try success -> 1 call, 0 sleeps.
2. Fail-fail-succeed -> 2 sleeps in [0.5, 1.0] and [0.75, 1.5].
3. All fail -> 3 calls, last result returned, no exception.
4. Unavailable adapter -> 1 call, 0 sleeps (FM2).
5. Deadline hit mid-loop -> stops early with attempts remaining (FM3), budget measured from loop start (R4).
6. `attempts: 1`, `backoff_factor: 0.5` -> clamped, no crash (R5).
7. Deadline with real `time.monotonic` injected -> all 3 attempts still run. Guards the absolute-vs-relative bug (R4).
8. `fn` returns `None` every time -> `ok=False` "adapter returned None", 3 attempts, caller safely reads `result.ok` (FM11).
9. `NonRetryableError` on attempt 2 of 3 -> propagates immediately, no third attempt (hatch 1).
10. Attempts spending 500/300/200 tokens -> `on_attempt` total 1000 while the returned result reports 200 (FM6).
11. `asyncio.CancelledError` on attempt 2 of 3 -> propagates immediately, no sleep, no fabricated result (FM12, hatch 3).
12. Custom `is_retryable` veto on an unexpected exception, attempt 2 of 3 -> that exception propagates (hatch 2).
13. Unexpected exceptions on attempts 1-2, success on 3 -> successful result returned, earlier exceptions visible only via `on_attempt`.
