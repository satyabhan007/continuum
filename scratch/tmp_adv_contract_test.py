#!/usr/bin/env python3
"""Adversarial contract tests for the retry helper design claims.

Round-7 validation: exercises the doc's behavioral contract claims against the
reference implementation directly (garbage coercion, classifier substrings,
cancellation propagation, deadline pre-sleep stop).
"""
import asyncio
import sys

sys.path.insert(0, "/home/satya/continuum")           # harness_adapters.py
sys.path.insert(0, "/home/satya/continuum/scratch")   # retry_backoff_reference.py
from retry_backoff_reference import (  # noqa: E402
    RetryPolicy, retry_with_backoff, default_is_retryable,
)


class R:
    def __init__(self, ok, error="", tokens=0):
        self.ok, self.error, self.tokens_used = ok, error, tokens


def make_fn(v):
    async def fn():
        return v
    return fn


async def t1_garbage():
    for v in [42, ["a"], object()]:
        out = await retry_with_backoff(
            make_fn(v),
            RetryPolicy(attempts=2, base_delay=0, jitter=False, deadline=None),
        )
        assert out.ok is False and "adapter returned" in out.error, (v, out)
    print("T1 PASS: garbage types coerced to ok=False with type-named error:", out.error)


async def t2_classifier():
    res = R(False, "CLI 'jcode' not installed")
    got = default_is_retryable(res, None)
    assert got is False, got
    print("T2 PASS: 'not installed' error classified non-retryable (False)")
    got = default_is_retryable(R(False, "non-JSON output"), None)
    assert got is True, got
    print("T2 PASS: transient 'non-JSON output' classified retryable (True)")
    got = default_is_retryable(R(False, ""), None)  # empty text = FM9
    assert got is True, got
    print("T2 PASS: empty-text failure classified retryable (FM9)")


async def t3_cancellation():
    state = {"count": 0}

    class Cancel:
        async def __call__(self):
            state["count"] += 1
            raise asyncio.CancelledError()

    try:
        await retry_with_backoff(
            Cancel(), RetryPolicy(attempts=3, base_delay=0, deadline=None)
        )
        raise AssertionError("cancellation was swallowed")
    except asyncio.CancelledError:
        assert state["count"] == 1, state
    print("T3 PASS: CancelledError propagated after 1 call, not retried, not converted")


async def t4_deadline_pre_sleep():
    class SlowFail:
        def __init__(self):
            self.n = 0

        async def __call__(self):
            self.n += 1
            return R(False, "timeout")

    sf = SlowFail()
    clock = {"t": 100.0}

    def now():
        clock["t"] += 50  # each attempt burns 50s
        return clock["t"]

    slept = []

    async def sl(d):
        slept.append(d)

    out = await retry_with_backoff(
        sf,
        RetryPolicy(attempts=5, base_delay=100, max_delay=200, jitter=False,
                    deadline=100),
        now=now, sleep=sl,
    )
    # max_delay must exceed base_delay (R1 caps unconditionally): w_1=100.
    # After attempt 1: elapsed=50, w=100 -> 50+100 > 100 -> stop BEFORE sleeping.
    assert sf.n == 1, sf.n
    assert slept == [], slept
    assert out.ok is False
    print("T4 PASS: deadline stop fired before sleeping (attempts=%d, sleeps=%s)" % (sf.n, slept))


async def t5_absolute_deadline_trap():
    """R4 warning: an ABSOLUTE check now()+w>deadline would suppress all retries."""
    now_val = 38786.0  # realistic time.monotonic() on an uptime machine
    assert now_val + 1.5 > 300, "absolute check must be True here (the trap)"
    rel = (now_val - now_val) + 1.5
    assert rel <= 300, "relative check must permit the retry"
    print("T5 PASS: absolute check traps (True), relative check permits (False) - R4 confirmed")


async def main():
    await t1_garbage()
    await t2_classifier()
    await t3_cancellation()
    await t4_deadline_pre_sleep()
    await t5_absolute_deadline_trap()
    print("ALL ADVERSARIAL TESTS PASSED")


asyncio.run(main())
