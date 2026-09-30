#!/usr/bin/env python3
"""scratch/validate_doc_claims_run1790758990.py - INDEPENDENT validation harness.

Round-2 validation for scratch/relay_test_run1790758990.md. Deliberately
does NOT reuse test vectors from scratch/test_retry_backoff_reference.py:
all scenarios use non-default parameters (attempts=5, factor=3.0,
base_delay=2.0, deadline=7.0, etc.) so a shared-authorship pass would not
mask a defect. Each case cites the exact doc claim (FM#/R#/hatch) it
validates.

Run: python3 scratch/validate_doc_claims_run1790758990.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness_adapters import AgentRunResult                    # noqa: E402
from retry_backoff_reference import (                          # noqa: E402
    NonRetryableError, RetryPolicy, default_is_retryable, retry_with_backoff,
)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}  [{detail}]")


class Clock:
    """Injected clock: starts at 5555.5 to punish absolute deadline math."""

    def __init__(self):
        self.t = 5555.5
        self.sleeps = []

    def now(self):
        return self.t

    async def sleep(self, d):
        self.sleeps.append(round(d, 4))
        self.t += d


def seq(items):
    state = {"n": 0}

    async def fn():
        i = state["n"]
        state["n"] += 1
        item = items[i]
        if isinstance(item, BaseException) or (
            isinstance(item, type) and issubclass(item, BaseException)):
            raise item
        return item
    return fn


async def main():
    # ---- FM2: non-retryable buried in a realistic stderr tail -> fail fast
    # Fresh vector: long stderr tail (adapters return err.decode()[-400:])
    # with 'not installed' mid-string, not at the start.
    clock = Clock()
    calls = []

    async def fn():
        calls.append(1)
        return AgentRunResult(
            ok=False, error="TypeError: 'NoneType' object is not iterable; "
            "hint: CLI 'jcode' not installed on this host; see docs")
    res = await retry_with_backoff(
        fn, RetryPolicy(attempts=5, base_delay=2.0, backoff_factor=3.0),
        sleep=clock.sleep, now=clock.now)
    check("FM2 fail-fast in stderr tail", len(calls) == 1 and not res.ok,
          f"calls={len(calls)}, sleeps={clock.sleeps}")

    # ---- R1: exponential schedule with non-default params
    # base=2.0 factor=3.0 attempts=5 max_delay=30: w = 2, 6, 18, min(30,54)=30
    clock = Clock()

    async def fn():
        return AgentRunResult(ok=False, error="transient blip")
    res = await retry_with_backoff(
        fn, RetryPolicy(attempts=5, base_delay=2.0, backoff_factor=3.0,
                        jitter=False), sleep=clock.sleep, now=clock.now)
    check("R1 schedule 2/6/18/30", clock.sleeps == [2.0, 6.0, 18.0, 30.0],
          f"sleeps={clock.sleeps}")

    # ---- R1 cap: unconditional max_delay even with deadline None
    #     base_delay=100 > max_delay=5, so EVERY wait caps: w1 = min(5, 100) = 5,
    #     w2 = min(5, 1000) = 5. (base_delay must EXCEED max_delay to hit the
    #     cap on the first wait - round-1 vector here was miscalculated.)
    clock = Clock()
    res = await retry_with_backoff(
        fn, RetryPolicy(attempts=3, base_delay=100.0, backoff_factor=10.0,
                        max_delay=5.0, jitter=False, deadline=None),
        sleep=clock.sleep, now=clock.now)
    check("R1 cap w/ deadline None", clock.sleeps == [5.0, 5.0],
          f"sleeps={clock.sleeps}")

    # ---- FM3/R4: deadline is RELATIVE; clock starts at 5555.5 (>> deadline 7)
    #     Absolute math would suppress ALL retries; relative must allow the
    #     first wait (2.0 <= 7) but stop before the second (2+6=8 > 7).
    clock = Clock()
    res = await retry_with_backoff(
        fn, RetryPolicy(attempts=5, base_delay=2.0, backoff_factor=3.0,
                        deadline=7.0, jitter=False),
        sleep=clock.sleep, now=clock.now)
    check("FM3/R4 relative deadline stops at budget",
          clock.sleeps == [2.0] and not res.ok, f"sleeps={clock.sleeps}")

    # ---- R4 corollary: at least one attempt always runs (tight deadline)
    clock = Clock()
    res = await retry_with_backoff(
        fn, RetryPolicy(attempts=5, base_delay=100.0, deadline=1.0,
                        jitter=False), sleep=clock.sleep, now=clock.now)
    check("R4 first attempt always runs", clock.sleeps == [] and not res.ok,
          f"sleeps={clock.sleeps}")

    # ---- R5: clamping - attempts<1 -> 1, factor<1 -> 1.0, negative base -> 0
    clock = Clock()
    res = await retry_with_backoff(
        fn, RetryPolicy(attempts=-4, backoff_factor=0.25, base_delay=-9.0),
        sleep=clock.sleep, now=clock.now)
    check("R5 clamps", clock.sleeps == [] and not res.ok,
          f"sleeps={clock.sleeps}")

    # ---- R6: zero-delay fast path (base_delay=0 -> no sleep calls at all)
    clock = Clock()
    res = await retry_with_backoff(
        fn, RetryPolicy(attempts=4, base_delay=0.0, backoff_factor=3.0,
                        jitter=False), sleep=clock.sleep, now=clock.now)
    check("R6 zero-delay skips sleep", clock.sleeps == [] and not res.ok,
          f"sleeps={clock.sleeps}")

    # ---- FM6: on_attempt aggregates tokens; return carries LAST only
    tokens = [1200, 340, 75, 910, 415]
    seen = []

    def on_attempt(n, result, exc):
        seen.append((n, getattr(result, "tokens_used", 0) or 0))
    res = await retry_with_backoff(
        seq([AgentRunResult(ok=False, error="x", tokens_used=t) for t in tokens]),
        RetryPolicy(attempts=5, base_delay=0.0), on_attempt=on_attempt,
        sleep=Clock().sleep, now=Clock().now)
    total = sum(t for _, t in seen)
    check("FM6 on_attempt sees all, return last only",
          total == sum(tokens) == 2940 and res.tokens_used == 415
          and [n for n, _ in seen] == [1, 2, 3, 4, 5],
          f"total={total}, returned={res.tokens_used}")

    # ---- FM11: fn returns None persistently -> synthesized result, no crash
    clock = Clock()

    async def none_fn():
        return None
    res = await retry_with_backoff(
        none_fn, RetryPolicy(attempts=3, base_delay=0.0),
        sleep=clock.sleep, now=clock.now)
    check("FM11 None -> synthesized ok=False",
          not res.ok and "adapter returned None" in res.error,
          f"error={res.error!r}")

    # ---- FM11 nuance: None then success -> success returned
    res = await retry_with_backoff(
        seq([None, AgentRunResult(ok=True, text="recovered")]),
        RetryPolicy(attempts=3, base_delay=0.0),
        sleep=clock.sleep, now=clock.now)
    check("FM11 None transient then success", res.ok and res.text == "recovered")

    # ---- FM11 round-2 gap: garbage NON-None return through the loop must
    #      not crash at result.ok; coerced + transient; recovery still works
    clock = Clock()

    async def garbage_fn():
        return "raw json string, not an AgentRunResult"
    res = await retry_with_backoff(
        garbage_fn, RetryPolicy(attempts=3, base_delay=0.0),
        sleep=clock.sleep, now=clock.now)
    check("FM11 garbage return -> coerced, no crash",
          not res.ok and "adapter returned str" in res.error,
          f"error={res.error!r}")

    res = await retry_with_backoff(
        seq(["garbage", {"ok": True}, AgentRunResult(ok=True, text="ok now")]),
        RetryPolicy(attempts=3, base_delay=0.0),
        sleep=clock.sleep, now=clock.now)
    check("FM11 garbage transient then success",
          res.ok and res.text == "ok now",
          f"text={getattr(res, 'text', None)!r}")

    # on_attempt must never observe raw garbage either (coercion happens first)
    seen_types = []

    def on_garbage(n, result, exc):
        seen_types.append(type(result).__name__
                          if result is not None else "NoneType")
    await retry_with_backoff(
        seq(["junk", AgentRunResult(ok=True, text="fine")]),
        RetryPolicy(attempts=2, base_delay=0.0), on_attempt=on_garbage,
        sleep=clock.sleep, now=clock.now)
    check("FM11 on_attempt sees coerced result, not garbage",
          seen_types == ["AgentRunResult", "AgentRunResult"],
          f"seen={seen_types}")

    # ---- FM12: cancellation propagates immediately, not retried/converted.
    #     The sleep between attempts 1->2 is legitimate; the claim under test
    #     is that NOTHING happens after the raising attempt: no further
    #     sleep, no third call, no fabricated result.
    clock = Clock()
    calls = []
    slept_at_cancel = [-1]

    async def cancel_fn():
        calls.append(1)
        if len(calls) == 2:
            slept_at_cancel[0] = len(clock.sleeps)
            raise asyncio.CancelledError()
        return AgentRunResult(ok=False, error="x")
    try:
        await retry_with_backoff(
            cancel_fn, RetryPolicy(attempts=3, base_delay=2.0),
            sleep=clock.sleep, now=clock.now)
        check("FM12 CancelledError propagates", False, "no exception raised")
    except asyncio.CancelledError:
        check("FM12 CancelledError propagates, nothing after raise",
              len(calls) == 2 and len(clock.sleeps) == slept_at_cancel[0],
              f"calls={len(calls)}, sleeps={clock.sleeps}, "
              f"slept_at_cancel={slept_at_cancel[0]}")

    # ---- FM12 variant: KeyboardInterrupt (BaseException) also escapes
    try:
        await retry_with_backoff(
            seq([KeyboardInterrupt()]),
            RetryPolicy(attempts=3, base_delay=0.0),
            sleep=clock.sleep, now=clock.now)
        check("FM12 KeyboardInterrupt propagates", False, "no exception raised")
    except KeyboardInterrupt:
        check("FM12 KeyboardInterrupt propagates", True)

    # ---- Hatch 1: NonRetryableError from a middle attempt, no further tries
    clock = Clock()
    mid_calls = []

    async def fn():
        mid_calls.append(1)
        if len(mid_calls) == 2:
            raise NonRetryableError("auth dance required")
        return AgentRunResult(ok=False, error="transient")
    try:
        await retry_with_backoff(
            fn, RetryPolicy(attempts=5, base_delay=2.0, backoff_factor=3.0),
            sleep=clock.sleep, now=clock.now)
        check("hatch 1 NonRetryableError", False, "no exception raised")
    except NonRetryableError:
        check("hatch 1 NonRetryableError", len(mid_calls) == 2,
              f"calls={len(mid_calls)} (must stop at 2nd)")

    # ---- Hatch 2: unexpected exc + custom is_retryable veto -> re-raised
    def veto(result, exc):
        return False
    try:
        await retry_with_backoff(
            seq([RuntimeError("adapter exploded")]),
            RetryPolicy(attempts=3, base_delay=0.0), is_retryable=veto,
            sleep=clock.sleep, now=clock.now)
        check("hatch 2 veto re-raises", False, "no exception raised")
    except RuntimeError as e:
        check("hatch 2 veto re-raises", "exploded" in str(e), f"{e}")

    # ---- Hatch 2 nuance: exc on attempts 1-2, success on 3 -> success wins
    res = await retry_with_backoff(
        seq([RuntimeError("boom1"), RuntimeError("boom2"),
             AgentRunResult(ok=True, text="third time")]),
        RetryPolicy(attempts=3, base_delay=0.0),
        sleep=clock.sleep, now=clock.now)
    check("hatch 2 later success wins", res.ok and res.text == "third time")

    # ---- FM10/FM11: classifier never crashes on odd inputs; garbage is
    #      transient, same philosophy as None (error substrings only apply to
    #      real results' .error field, never to the garbage object itself)
    odd = [None, "", "credentials leak", object(), 5, {"ok": True}]
    got = [default_is_retryable(o, None) for o in odd]
    ok_safe = all(isinstance(v, bool) for v in got)
    check("FM10/FM11 classifier safe, garbage transient",
          ok_safe and all(got)
          and not default_is_retryable(AgentRunResult(ok=True), None),
          f"got={got}")

    # ---- FM10/FM2 classifier matrix on fresh realistic error strings
    matrix = [
        ("opencode auth.json present but credentials rejected by server", False),
        ("opencode not authenticated - run: opencode auth login", False),
        ("antigravity OAuth not configured for this user", False),
        ("cline is a VS Code extension with no headless CLI", False),
        ("TypeError: 'NoneType' has no attribute 'strip'", True),
        ("non-JSON output from harness", True),
        ("TimeoutError: timed out", True),
        ("", True),
    ]
    bad = [(s, default_is_retryable(AgentRunResult(ok=False, error=s), None), want)
           for s, want in matrix]
    bad = [(s, got, want) for s, got, want in bad if got != want]
    check("FM10/FM2 classifier matrix", not bad, f"mismatches={bad}")

    # ---- R5 from_config: real execution_settings-shaped dict, partial keys
    pol = RetryPolicy.from_config({"retry_attempts": 7, "backoff_factor": 2.0})
    check("R5 from_config partial keys -> defaults for rest",
          pol.attempts == 7 and pol.backoff_factor == 2.0
          and pol.base_delay == 1.0 and pol.max_delay == 30.0
          and pol.deadline == 300.0 and pol.jitter is True,
          f"attempts={pol.attempts}, factor={pol.backoff_factor}, "
          f"deadline={pol.deadline}")

    # ---- R5 from_config: falsy deadline -> None; None input -> all defaults
    pol = RetryPolicy.from_config({"max_execution_time_seconds": 0})
    check("R5 falsy deadline -> None", pol.deadline is None)
    pol = RetryPolicy.from_config(None)
    check("R5 None config -> defaults",
          pol.attempts == 3 and pol.backoff_factor == 1.5
          and pol.deadline == 300.0)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
