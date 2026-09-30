"""scratch/retry_backoff_reference.py - reference implementation of the micro-design.

VALIDATION ARTIFACT, not production code. Exists solely so the 16-case test
plan in scratch/retry_with_backoff_microdesign.md can be executed against a
real loop. Implements the design exactly as specified there:

  - RetryPolicy.from_config (R5 clamping, existing config keys source of truth)
  - default_is_retryable (FM2 fail-fast substrings, verified against the
    actual error strings harness_adapters.py produces)
  - retry_with_backoff (R1-R7 timing, FM1/FM2/FM3/FM6/FM11/FM12 behavior,
    escape hatches 1-3)
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from harness_adapters import AgentRunResult

log = logging.getLogger("retry-backoff-scratch")

# Non-retryable substrings. Every string is grounded in a literal error
# message produced by harness_adapters.py (check() / run() failure paths):
#   "not installed"        BaseAdapter.check / AntigravityAdapter / opencode
#   "not configured"       AntigravityAdapter ("antigravity OAuth not configured")
#   "not authenticated"    OpencodeAdapter.check ("opencode not authenticated")
#   "credentials"          OpencodeAdapter invalid-key paths (check + live probe)
#   "no headless"          ClineAdapter ("no headless CLI")
_NON_RETRYABLE_SUBSTRINGS = (
    "not installed",
    "not configured",
    "not authenticated",
    "credentials",
    "no headless",
)


@dataclass
class RetryPolicy:
    attempts: int = 3                    # TOTAL tries: initial + (attempts-1) retries
    backoff_factor: float = 1.5
    base_delay: float = 1.0              # seconds, wait before the FIRST retry
    max_delay: float = 30.0              # cap on any single wait
    deadline: Optional[float] = 300.0    # RELATIVE budget from loop entry (R4)
    jitter: bool = True                  # equal jitter (R2)

    @classmethod
    def from_config(cls, s: Optional[dict]) -> "RetryPolicy":
        """execution_settings -> policy. Existing keys stay the source of truth.

        Absent/nested-missing keys fall back to these code defaults, which is
        what makes the helper tolerant of partial config dicts: load_config's
        merge is SHALLOW ({**defaults, **file}), so a file's execution_settings
        replaces the default dict wholesale and may lack any key.
        """
        s = s or {}
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
    if result is None and exc is None:
        return True   # fn returned None: contract violation, transient (FM11)
    if result is not None and not getattr(result, "ok", False):
        # getattr (not result.ok): garbage without .ok must not crash the
        # classifier either (FM10/FM11); it reads as a failed transient
        # attempt, same philosophy as None.
        err = (getattr(result, "error", "") or "").lower()
        if any(s in err for s in _NON_RETRYABLE_SUBSTRINGS):
            return False
        return True   # rc != 0, non-JSON output, empty text, timeouts
    return exc is not None   # defensive: adapters promise never to raise


def _computed_wait(policy: "RetryPolicy", k: int) -> float:
    """R1: w_k = min(max_delay, base_delay * factor ** (k-1))."""
    return min(policy.max_delay, policy.base_delay * policy.backoff_factor ** (k - 1))


def _actual_wait(policy: "RetryPolicy", k: int) -> float:
    """R2 equal jitter: uniform in [w_k/2, w_k]."""
    w = _computed_wait(policy, k)
    if not policy.jitter:
        return w
    return w / 2 + random.uniform(0, w / 2)


async def retry_with_backoff(
    fn: Callable[[], Awaitable[Any]],
    policy: RetryPolicy,
    is_retryable: Callable[[Any, Optional[BaseException]], bool] = default_is_retryable,
    on_attempt: Optional[Callable[[int, Any, Optional[BaseException]], None]] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], float] = time.monotonic,
) -> Any:
    """Call fn() up to policy.attempts times with exponential backoff.

    Ordinary failures never raise: the last result is returned. Escape
    hatches: (1) NonRetryableError from any attempt, immediately; (2) an
    unexpected exception re-raised when the loop can proceed no further
    (attempts exhausted, is_retryable veto, or deadline stop); (3)
    cancellation propagates the instant fn raises it (Exception, not
    BaseException, is caught).
    """
    start = now()                          # captured ONCE: budget is relative (R4)
    last_result: Any = None
    last_exc: Optional[BaseException] = None

    for attempt in range(1, policy.attempts + 1):
        result, exc = None, None
        try:
            result = await fn()
        except Exception as e:             # NOT BaseException: FM12
            exc = e
        if exc is None and result is not None and not hasattr(result, "ok"):
            # FM11 (broadened in round-2 validation): a contract-violating
            # adapter may return garbage that is not None and not an
            # AgentRunResult. Coerce BEFORE on_attempt and the classifier so
            # nothing downstream can crash on a missing .ok attribute.
            result = AgentRunResult(
                ok=False, error=f"adapter returned {type(result).__name__}")
        if on_attempt is not None:
            on_attempt(attempt, result, exc)
        if isinstance(exc, NonRetryableError):
            raise exc                     # hatch 1: fail fast, any attempt
        if exc is None and result is not None and result.ok:
            return result
        last_result, last_exc = result, exc

        # Terminal? attempts exhausted, classifier veto, or last attempt.
        if attempt >= policy.attempts or not is_retryable(result, exc):
            break

        w = _actual_wait(policy, attempt)   # wait before retry k=attempt
        # R4: deadline is relative to loop entry; checked BEFORE sleeping.
        if policy.deadline is not None and (now() - start) + w > policy.deadline:
            log.warning("deadline_exceeded: stopping after attempt %d "
                        "(wait %.2fs would pass budget %.2fs)",
                        attempt, w, policy.deadline)
            break
        if w > 0:                          # R6: zero delay skips sleep entirely
            await sleep(w)

    # Terminal handling.
    if last_exc is not None:
        raise last_exc                     # hatch 2
    if last_result is None:
        # FM11: contract-violating adapter. Caller must never see None.
        return AgentRunResult(ok=False, error="adapter returned None")
    return last_result                     # FM1: last result, no exception
