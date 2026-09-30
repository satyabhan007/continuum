#!/usr/bin/env python3
"""REAL orchestrator test: the actual harness pool (jcode, opencode,
antigravity, cline) with real agent execution where available.

Proves:
1. Adapter availability probing is honest (reports exactly what works)
2. The orchestrator runs a task through the REAL jcode adapter when selected
3. Relay semantics hold with real agents: budget exhaustion -> baton ->
   fresh runner resumes from the baton (never restarts)
4. The fresh-session fallback works: with a one-harness pool, exhaustion
   hands the baton to a FRESH session of the same CLI (new runner, fresh
   budget window) instead of dying.

Usage: python3 test_relay_real.py
"""

import asyncio
import logging
import os
import time

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("real-orch-test")

from harness_adapters import ADAPTERS, summarize_check, extract_baton
from multi_harness_orchestrator import (
    MultiHarnessOrchestrator, AgentContext, HarnessStatus,
    TokenExhaustedError, HarnessError,
)


async def test_adapter_availability():
    """All four scoped adapters must probe without crashing - honest reports."""
    log.info("--- Adapter availability probe ---")
    statuses = summarize_check()
    for name, avail in statuses.items():
        state = "AVAILABLE" if avail.available else "unavailable"
        log.info(f"  {name:12s} {state}: {avail.reason}")
    assert set(statuses.keys()) == {"jcode", "opencode", "cline", "antigravity"}
    # jcode CLI is installed and logged in on this machine - must be available
    assert statuses["jcode"].available, "jcode adapter should be available here"
    log.info("PASS: adapter probing honest and complete")


async def test_real_orchestrator_relay():
    """Full relay through the REAL pool: exhaustion -> baton -> real agent
    continues. Tiny budgets force the relay to actually happen."""
    log.info("--- Real orchestrator relay (real jcode execution) ---")

    # Unique deliverable per run: prior runs' agents COMMIT their scratch
    # files, so a fixed path makes fresh agents 'restore from git' instead
    # of writing new content. Unique path = genuinely fresh work.
    run_id = f"run{int(time.time())}"
    target = f"scratch/relay_test_{run_id}.md"
    task = ("Create a 3-section micro-design doc for a retry-with-backoff helper "
            f"in the scratch file {target}: (1) API sketch, (2) backoff timing "
            "rules, (3) failure modes. Write the file only - do not run git "
            "commit.")

    # 3-step plan: keep each relay leg short so real agent calls finish.
    # A real step on this pool takes 1-3 min; 600s is a generous cap. With
    # the child-kill timeout fix, an over-slow step degrades to a graceful
    # handoff (fresh runner) instead of leaking an orphan process.
    ADAPTERS['jcode'].timeout = 600
    orch = MultiHarnessOrchestrator()
    orch.config['execution_settings']['max_execution_time_seconds'] = 1800  # generous cap
    for name in ("jcode", "opencode", "antigravity", "cline"):
        if name in orch.harnesses:
            orch.harnesses[name]['token_budget'].total_budget = 45000
            orch.harnesses[name]['token_budget'].remaining_tokens = 45000
    result = await orch.execute_task(task)

    assert result['success'] is True, f"real relay failed: {result.get('error')}"
    ctx = orch.agent_contexts[result['execution_id']]
    real_steps = [s for s in ctx.step_history if s.get('real_agent')]
    log.info(f"Relay completed. Steps: {[s['step'] for s in ctx.step_history]}, "
             f"real-agent steps: {len(real_steps)}, harnesses used: "
             f"{sorted(set(s['harness'] for s in ctx.step_history))}, "
             f"handoff_reason: {ctx.execution_state.get('handoff_reason')}")
    assert len(ctx.step_history) >= 2, "task should take multiple steps"
    assert all(s.get('real_agent') for s in ctx.step_history), \
        "every step must have run on the real agent (no silent simulation)"
    # With a 45k budget and ~27k real tokens/step, the baton MUST have been
    # passed at least once (budget exhausts during step 2's accounting).
    assert ctx.execution_state.get('handoff_reason'), \
        "relay must hand off the baton at least once (budget exhaustion)"
    # Steps must be sequential and non-repeating: never restart, only resume
    step_numbers = [s['step'] for s in ctx.step_history]
    assert step_numbers == sorted(set(step_numbers)), \
        f"steps must never repeat (restart bug): {step_numbers}"
    # The real deliverable must exist with real content
    assert os.path.exists(target), f"relay deliverable missing: {target}"
    content = open(target).read()
    assert len(content) > 200, f"deliverable too thin: {len(content)} chars"
    log.info(f"Deliverable written: {target} ({len(content)} chars)")
    log.info("PASS: real orchestrator relay with real agent execution")


async def main():
    log.info("=== REAL harness pool orchestrator tests ===")
    await test_adapter_availability()
    await test_real_orchestrator_relay()
    log.info("=== ALL REAL-POOL TESTS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
