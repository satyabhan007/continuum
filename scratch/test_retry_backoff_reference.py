#!/usr/bin/env python3
"""scratch/test_retry_backoff_reference.py - executes the micro-design test plan.

Runs the 18 validation case functions (the doc appendix's 13, plus 5 grounded
extras: 6b negative-delay clamp, 8b garbage-return coercion, 14 classifier
matrix, 15 no-deadline, 15b unconditional max_delay cap) against
scratch/retry_backoff_reference.py. All sleeps/clocks are injected so
the suite finishes in ~0.1s with no real waiting.

Run: python3 scratch/test_retry_backoff_reference.py
"""

import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness_adapters import AgentRunResult          # noqa: E402
from retry_backoff_reference import (                # noqa: E402
    NonRetryableError, RetryPolicy, default_is_retryable, retry_with_backoff,
)

PASS, FAIL = 0, 0


class FakeClock:
    """Injected now/sleep. t starts at 1000.0, NOT zero, so any absolute
    (now() > deadline) comparison in the implementation would trip here."""

    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def now(self):
        return self.t

    async def sleep(self, d):
        self.sleeps.append(d)
        self.t += d


class NoopSleep:
    """Records sleeps without advancing time (used with the REAL clock)."""

    def __init__(self):
        self.sleeps = []

    async def sleep(self, d):
        self.sleeps.append(d)


class LogCap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}  [{detail}]")


def seq_fn(items):
    """fn() pops items in order; exception instances/classes are raised."""
    state = {"n": 0}

    async def fn():
        i = state["n"]
        state["n"] += 1
        item = items[i]
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item()
        if isinstance(item, BaseException):
            raise item
        return item
    return fn, state


def recorder():
    rec = {"attempts": [], "tokens": 0}

    def on_attempt(no, result, exc):
        rec["attempts"].append((no, result, exc))
        rec["tokens"] += getattr(result, "tokens_used", 0) or 0
    return rec, on_attempt


def R(ok=True, text="", tokens=0, error=""):
    return AgentRunResult(ok=ok, text=text, tokens_used=tokens, error=error)


# ---------------------------------------------------------------- case 1
def case_1_first_try_success():
    fn, state = seq_fn([R(ok=True, text="done", tokens=100)])
    clock = FakeClock()
    rec, on_attempt = recorder()
    res = asyncio.run(retry_with_backoff(
        fn, RetryPolicy(attempts=3, jitter=False), on_attempt=on_attempt,
        sleep=clock.sleep, now=clock.now))
    check("1 calls==1", state["n"] == 1, f"calls={state['n']}")
    check("1 sleeps==0", clock.sleeps == [])
    check("1 returns success", res.ok and res.text == "done")
    check("1 on_attempt saw 1 attempt", len(rec["attempts"]) == 1)
    check("1 tokens==100", rec["tokens"] == 100)


# ---------------------------------------------------------------- case 2
def case_2_fail_fail_succeed():
    items = [R(ok=False, tokens=500, error="non-JSON output"),
             R(ok=False, tokens=300, error="TimeoutError: timed out"),
             R(ok=True, text="ok", tokens=200)]
    fn, state = seq_fn(items)
    clock = FakeClock()
    rec, on_attempt = recorder()
    res = asyncio.run(retry_with_backoff(
        fn, RetryPolicy(attempts=3, backoff_factor=1.5, base_delay=1.0,
                        max_delay=30.0, jitter=True),
        on_attempt=on_attempt, sleep=clock.sleep, now=clock.now))
    check("2 calls==3", state["n"] == 3, f"calls={state['n']}")
    check("2 two sleeps", len(clock.sleeps) == 2, f"sleeps={clock.sleeps}")
    s1, s2 = (clock.sleeps + [0, 0])[:2]
    check("2 sleep1 in [0.5,1.0]", 0.5 <= s1 <= 1.0, f"{s1:.3f}")
    check("2 sleep2 in [0.75,1.5]", 0.75 <= s2 <= 1.5, f"{s2:.3f}")
    check("2 FM6 aggregate tokens==1000", rec["tokens"] == 1000,
          f"tokens={rec['tokens']}")
    check("2 returned result is last (200)", res.tokens_used == 200)
    check("2 on_attempt saw all 3", len(rec["attempts"]) == 3)


# ---------------------------------------------------------------- case 3
def case_3_all_fail():
    items = [R(ok=False, error="non-JSON output") for _ in range(3)]
    fn, state = seq_fn(items)
    clock = FakeClock()
    try:
        res = asyncio.run(retry_with_backoff(
            fn, RetryPolicy(attempts=3, jitter=False),
            sleep=clock.sleep, now=clock.now))
    except Exception as e:
        check("3 no exception", False, f"raised {type(e).__name__}")
        return
    check("3 no exception", True)
    check("3 calls==3", state["n"] == 3, f"calls={state['n']}")
    check("3 returns LAST result object", res is items[-1])
    check("3 two sleeps", len(clock.sleeps) == 2)


# ------------------------------------------------- case 4 + 4b (FM2 matrix)
REAL_ERROR_STRINGS = [
    "CLI 'jcode' not installed",                       # BaseAdapter.check
    "opencode CLI not installed",                       # OpencodeAdapter.check
    "opencode not authenticated - run: opencode auth login",
    "opencode credentials present but invalid (live probe failed)",
    "opencode auth.json present but credentials invalid/rejected (live probe failed)",
    "antigravity OAuth not configured (run: jcode login --provider antigravity)",
    "cline is a VS Code extension with no headless CLI; "
    "wire it via its MCP/HTTP bridge when available",
]


def case_4_unavailable_adapter():
    for s in REAL_ERROR_STRINGS:
        fn, state = seq_fn([R(ok=False, error=s), R(ok=True)])
        clock = FakeClock()
        res = asyncio.run(retry_with_backoff(
            fn, RetryPolicy(attempts=3, jitter=False),
            sleep=clock.sleep, now=clock.now))
        label = s[:40]
        check(f"4 FM2 1 call: {label}...", state["n"] == 1, f"calls={state['n']}")
        check(f"4 FM2 0 sleeps: {label}...", clock.sleeps == [])
        check(f"4 FM2 returns failed result: {label}...", res.ok is False)


# ---------------------------------------------------------------- case 5
def case_5_deadline_mid_loop():
    fn, state = seq_fn([R(ok=False, error="TimeoutError: timed out")] * 3)
    clock = FakeClock()
    cap = LogCap()
    logging.getLogger("retry-backoff-scratch").addHandler(cap)
    try:
        res = asyncio.run(retry_with_backoff(
            fn, RetryPolicy(attempts=3, base_delay=10.0, backoff_factor=1.5,
                             deadline=15.0, jitter=False),
            sleep=clock.sleep, now=clock.now))
        ok_raised = True
    except Exception:
        ok_raised = False
        res = None
    check("5 no exception (returns result)", ok_raised)
    check("5 stops early: calls==2", state["n"] == 2, f"calls={state['n']}")
    check("5 one sleep happened (w1=10 <= budget)", clock.sleeps == [10.0],
          f"sleeps={clock.sleeps}")
    check("5 w2=15 rejected (10+15>15)", res is not None and res.ok is False)
    check("5 logs deadline_exceeded",
          any("deadline_exceeded" in m for m in cap.messages),
          f"msgs={cap.messages}")


# ---------------------------------------------------------------- case 6
def case_6_clamping():
    p = RetryPolicy.from_config({"retry_attempts": 1, "backoff_factor": 0.5,
                                 "max_execution_time_seconds": 300})
    check("6 attempts clamped 1->1", p.attempts == 1, f"{p.attempts}")
    check("6 factor 0.5->1.0", p.backoff_factor == 1.0, f"{p.backoff_factor}")
    fn, state = seq_fn([R(ok=False, error="non-JSON output"), R(ok=True)])
    clock = FakeClock()
    res = asyncio.run(retry_with_backoff(
        fn, p, sleep=clock.sleep, now=clock.now))
    check("6 single attempt, no crash", state["n"] == 1 and res.ok is False)
    check("6 zero sleeps", clock.sleeps == [])


def case_6b_negative_delay():
    p = RetryPolicy.from_config({"retry_attempts": 3, "backoff_factor": 1.5,
                                 "retry_base_delay": -5,
                                 "max_execution_time_seconds": 300})
    check("6b base_delay -5 -> 0", p.base_delay == 0, f"{p.base_delay}")
    fn, state = seq_fn([R(ok=False, error="x")] * 3)
    clock = FakeClock()
    asyncio.run(retry_with_backoff(fn, p, sleep=clock.sleep, now=clock.now))
    check("6b R6: 3 calls, sleep call skipped", state["n"] == 3 and clock.sleeps == [],
          f"calls={state['n']} sleeps={clock.sleeps}")


# ---------------------------------------------------------------- case 7
def case_7_real_monotonic_deadline():
    """R4 guard: with the REAL time.monotonic as now, a 300s budget must
    still allow all 3 attempts. An absolute (now()+w>deadline) check reads
    monotonic ~134k and would silently stop after attempt 1."""
    fn, state = seq_fn([R(ok=False, error="TimeoutError: timed out")] * 3)
    noop = NoopSleep()
    res = asyncio.run(retry_with_backoff(
        fn, RetryPolicy(attempts=3, deadline=300.0, jitter=False),
        sleep=noop.sleep, now=time.monotonic))
    check("7 all 3 attempts run with real monotonic",
          state["n"] == 3, f"calls={state['n']}")
    check("7 both sleeps taken", noop.sleeps == [1.0, 1.5], f"{noop.sleeps}")
    check("7 result returned, not raised", res.ok is False)


# ---------------------------------------------------------------- case 8
def case_8_none_return():
    fn, state = seq_fn([None, None, None, R(ok=True)])
    clock = FakeClock()
    rec, on_attempt = recorder()
    res = asyncio.run(retry_with_backoff(
        fn, RetryPolicy(attempts=3, jitter=False), on_attempt=on_attempt,
        sleep=clock.sleep, now=clock.now))
    check("8 3 attempts made", state["n"] == 3, f"calls={state['n']}")
    check("8 returns AgentRunResult, not None", isinstance(res, AgentRunResult))
    check("8 ok is False", res.ok is False)
    check("8 error names the violation",
          res.error == "adapter returned None", f"error={res.error!r}")
    check("8 caller can read result.ok safely", res.ok in (True, False))
    check("8 classifier treats None/None as transient",
          default_is_retryable(None, None) is True)


# ---------------------------------------------------------------- case 8b
def case_8b_garbage_return():
    """FM11 broadened: garbage (not None, no .ok) must not crash anything.

    The doc's pre-fix classifier did `result.ok` directly; a string return
    would AttributeError, violating FM10's "never crashes" claim. The
    reference coerces garbage to AgentRunResult(ok=False, error="adapter
    returned <TypeName>") BEFORE on_attempt and classification.
    """
    fn, state = seq_fn(["Command timed out", "crashed", "still garbage"])
    clock = FakeClock()
    rec, on_attempt = recorder()
    res = asyncio.run(retry_with_backoff(
        fn, RetryPolicy(attempts=3, jitter=False), on_attempt=on_attempt,
        sleep=clock.sleep, now=clock.now))
    check("8b all 3 attempts made", state["n"] == 3, f"calls={state['n']}")
    check("8b returns AgentRunResult, not garbage",
          isinstance(res, AgentRunResult))
    check("8b ok is False", res.ok is False)
    check("8b error names the type",
          res.error == "adapter returned str", f"error={res.error!r}")
    check("8b on_attempt never saw raw garbage",
          all(isinstance(r, AgentRunResult) for _, r, _ in rec["attempts"]))
    check("8b classifier never crashed on garbage",
          default_is_retryable("Command timed out", None) is True)


# ---------------------------------------------------------------- case 9
def case_9_nonretryable():
    items = [R(ok=False, error="non-JSON output"),
             NonRetryableError("bad prompt: refused"),
             R(ok=True)]
    fn, state = seq_fn(items)
    clock = FakeClock()
    rec, on_attempt = recorder()
    try:
        asyncio.run(retry_with_backoff(
            fn, RetryPolicy(attempts=3, jitter=False), on_attempt=on_attempt,
            sleep=clock.sleep, now=clock.now))
        check("9 NonRetryableError propagates", False, "no exception raised")
        return
    except NonRetryableError:
        check("9 NonRetryableError propagates", True)
    check("9 stops at attempt 2 (no 3rd call)", state["n"] == 2, f"calls={state['n']}")
    check("9 on_attempt saw both attempts", len(rec["attempts"]) == 2)
    check("9 second attempt reported the exception",
          rec["attempts"][1][2] is items[1])
    check("9 no sleep after the fail-fast attempt",
          clock.sleeps == [1.0], f"sleeps={clock.sleeps}")


# ---------------------------------------------------------------- case 10
def case_10_token_aggregation():
    """Explicit FM6 case (same scenario as case 2, isolated): 500/300/200."""
    items = [R(ok=False, tokens=500, error="TimeoutError"),
             R(ok=False, tokens=300, error="TimeoutError"),
             R(ok=True, tokens=200)]
    fn, state = seq_fn(items)
    clock = FakeClock()
    rec, on_attempt = recorder()
    res = asyncio.run(retry_with_backoff(
        fn, RetryPolicy(attempts=3, jitter=False), on_attempt=on_attempt,
        sleep=clock.sleep, now=clock.now))
    check("10 aggregate spend 1000", rec["tokens"] == 1000, f"{rec['tokens']}")
    check("10 returned result reports only winner (200)",
          res.tokens_used == 200, f"{res.tokens_used}")


# ---------------------------------------------------------------- case 11
def case_11_cancellation():
    check("11 CancelledError is BaseException, not Exception",
          issubclass(asyncio.CancelledError, BaseException)
          and not issubclass(asyncio.CancelledError, Exception))
    items = [R(ok=False, error="TimeoutError: timed out"),
             asyncio.CancelledError(),
             R(ok=True)]
    fn, state = seq_fn(items)
    clock = FakeClock()
    rec, on_attempt = recorder()
    try:
        asyncio.run(retry_with_backoff(
            fn, RetryPolicy(attempts=3, jitter=False), on_attempt=on_attempt,
            sleep=clock.sleep, now=clock.now))
        check("11 CancelledError propagates", False, "no exception raised")
        return
    except asyncio.CancelledError:
        check("11 CancelledError propagates", True)
    check("11 no 3rd attempt", state["n"] == 2, f"calls={state['n']}")
    check("11 no sleep after cancellation",
          clock.sleeps == [1.0], f"sleeps={clock.sleeps}")
    check("11 cancelled attempt never fired on_attempt",
          len(rec["attempts"]) == 1, f"attempts={len(rec['attempts'])}")


# ---------------------------------------------------------------- case 12
def case_12_custom_veto():
    items = [R(ok=False, error="non-JSON output"),
             ValueError("boom"),
             R(ok=True)]
    fn, state = seq_fn(items)

    def veto(result, exc):
        if isinstance(exc, ValueError):
            return False
        return default_is_retryable(result, exc)

    try:
        asyncio.run(retry_with_backoff(
            fn, RetryPolicy(attempts=3, jitter=False), is_retryable=veto,
            sleep=FakeClock().sleep, now=FakeClock().now))
        check("12 vetoed exception propagates", False, "no exception raised")
        return
    except ValueError as e:
        check("12 vetoed exception propagates", True, f"{e}")
    check("12 no 3rd attempt", state["n"] == 2, f"calls={state['n']}")


# ---------------------------------------------------------------- case 13
def case_13_exceptions_then_success():
    items = [ValueError("a"), ValueError("b"), R(ok=True, text="ok")]
    fn, state = seq_fn(items)
    clock = FakeClock()
    rec, on_attempt = recorder()
    res = asyncio.run(retry_with_backoff(
        fn, RetryPolicy(attempts=3, jitter=False), on_attempt=on_attempt,
        sleep=clock.sleep, now=clock.now))
    check("13 success returned despite earlier exceptions",
          res.ok and res.text == "ok")
    check("13 all 3 attempts ran", state["n"] == 3, f"calls={state['n']}")
    excs = [e for (_, _, e) in rec["attempts"]]
    check("13 earlier exceptions observable only via on_attempt",
          isinstance(excs[0], ValueError) and isinstance(excs[1], ValueError)
          and excs[2] is None)


# ---------------------------------------------------------------- case 14
def case_14_classifier_matrix():
    for s in REAL_ERROR_STRINGS:
        check(f"14 non-retryable: {s[:40]}...",
              default_is_retryable(R(ok=False, error=s), None) is False)
    for s in ["non-JSON output", "TimeoutError: timed out", ""]:
        check(f"14 transient (retryable): {s!r}",
              default_is_retryable(R(ok=False, error=s), None) is True)
    check("14 NonRetryableError -> False",
          default_is_retryable(None, NonRetryableError()) is False)
    check("14 unexpected exception -> True (defensive)",
          default_is_retryable(None, ValueError()) is True)
    check("14 None result, no exc -> True (FM11)",
          default_is_retryable(None, None) is True)


# ---------------------------------------------------------------- case 15
def case_15_no_deadline():
    # retry_max_delay/ retry_jitter supplied so the waits are deterministic
    # and UNCAPPED: without them from_config defaults cap at 30s (R1's min()
    # applies even with no deadline) and jitter halves them (R2).
    p = RetryPolicy.from_config({"retry_attempts": 3, "backoff_factor": 1.5,
                                 "retry_base_delay": 100,
                                 "retry_max_delay": 1000,
                                 "retry_jitter": False,
                                 "max_execution_time_seconds": 0})
    check("15 falsy max_execution_time -> None deadline",
          p.deadline is None, f"{p.deadline}")
    fn, state = seq_fn([R(ok=False, error="x")] * 3)
    clock = FakeClock()
    asyncio.run(retry_with_backoff(fn, p, sleep=clock.sleep, now=clock.now))
    check("15 no deadline stop: all attempts run",
          state["n"] == 3, f"calls={state['n']}")
    check("15 huge waits allowed without deadline",
          clock.sleeps == [100.0, 150.0], f"sleeps={clock.sleeps}")


def case_15b_max_delay_cap():
    """R1's min(max_delay, ...) applies UNCONDITIONALLY: base_delay=100 with
    the default max_delay=30 must produce capped (not 100s) waits."""
    p = RetryPolicy.from_config({"retry_attempts": 3, "backoff_factor": 1.5,
                                 "retry_base_delay": 100,
                                 "retry_jitter": False,
                                 "max_execution_time_seconds": 0})
    fn, state = seq_fn([R(ok=False, error="x")] * 3)
    clock = FakeClock()
    asyncio.run(retry_with_backoff(fn, p, sleep=clock.sleep, now=clock.now))
    check("15b max_delay caps even without deadline",
          clock.sleeps == [30.0, 30.0], f"sleeps={clock.sleeps}")


def main():
    logging.getLogger("retry-backoff-scratch").setLevel(logging.WARNING)
    for case in (case_1_first_try_success, case_2_fail_fail_succeed,
                 case_3_all_fail, case_4_unavailable_adapter,
                 case_5_deadline_mid_loop, case_6_clamping, case_6b_negative_delay,
                 case_7_real_monotonic_deadline, case_8_none_return,
                 case_8b_garbage_return, case_9_nonretryable, case_10_token_aggregation,
                 case_11_cancellation, case_12_custom_veto,
                 case_13_exceptions_then_success, case_14_classifier_matrix,
                 case_15_no_deadline, case_15b_max_delay_cap):
        print(f"{case.__name__}:")
        case()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
