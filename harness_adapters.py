#!/usr/bin/env python3
"""Real harness adapters: connect the orchestrator to actual agent CLIs.

Supported harnesses (per project scope):
  - jcode        headless via `jcode run --json <msg>`
  - opencode     headless via `opencode run <msg>`  (needs its own auth)
  - cline        VS Code extension - no headless CLI; adapter probes and
                 reports unavailable rather than pretending
  - antigravity  Google's agent IDE - reachable via `jcode -p antigravity`
                 if OAuth'd; adapter probes and falls back

Philosophy: same as the relay - NEVER crash, always degrade gracefully.
An adapter that is not configured returns an Availability showing that,
and the orchestrator treats it like a harness with no remaining budget
(available for baton receipt, but not selected for execution until real).

Each adapter implements run(prompt) -> AgentRunResult with:
  ok, text, tokens_used, provider, model, error
"""

import ast
import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional

log = logging.getLogger("harness-adapters")

BATON_OPEN = "===BATON==="
BATON_CLOSE = "===END==="


@dataclass
class AgentRunResult:
    ok: bool
    text: str = ""
    tokens_used: int = 0
    provider: str = ""
    model: str = ""
    error: str = ""
    session_id: str = ""


@dataclass
class Availability:
    available: bool
    reason: str = ""


class BaseAdapter:
    """Common headless-CLI adapter machinery."""

    name = "base"
    cli = None  # executable name

    def __init__(self, timeout: int = 600):
        self.timeout = timeout

    # -- availability ------------------------------------------------
    def check(self) -> Availability:
        """Fast probe: is this agent usable right now?"""
        if not self.cli or not shutil.which(self.cli):
            return Availability(False, f"CLI '{self.cli}' not installed")
        return Availability(True, "ready")

    # -- execution ---------------------------------------------------
    async def run(self, prompt: str, workdir: str = None) -> AgentRunResult:
        """Run the agent headlessly. Must never raise - returns ok=False."""
        avail = self.check()
        if not avail.available:
            return AgentRunResult(ok=False, error=avail.reason)
        try:
            return await self._run_checked(prompt, workdir)
        except Exception as e:
            return AgentRunResult(ok=False, error=f"{type(e).__name__}: {e}")

    async def _run_checked(self, prompt: str, workdir: str) -> AgentRunResult:
        raise NotImplementedError

    # -- helpers -----------------------------------------------------
    @staticmethod
    def _parse_tokens(usage: Optional[dict]) -> int:
        """jcode-style usage dict -> total tokens."""
        if not usage:
            return 0
        total = 0
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                  "cache_creation_input_tokens"):
            v = usage.get(k)
            if isinstance(v, int):
                total += v
        return total

    def _from_json_result(self, d: dict) -> AgentRunResult:
        text = d.get("text") or d.get("response") or ""
        usage = d.get("usage")
        if isinstance(usage, str):
            try:
                usage = ast.literal_eval(usage)  # jcode emits a dict-repr string
            except Exception:
                usage = None
        tokens = self._parse_tokens(usage) if isinstance(usage, dict) else 0
        return AgentRunResult(
            ok=bool(text),
            text=text,
            tokens_used=tokens,
            provider=d.get("provider", ""),
            model=d.get("model", ""),
            session_id=d.get("session_id", ""),
        )


class JcodeAdapter(BaseAdapter):
    """jcode CLI - full headless support via `jcode run --json`."""

    name = "jcode"
    cli = "jcode"

    async def _run_checked(self, prompt: str, workdir: str) -> AgentRunResult:
        proc = await asyncio.create_subprocess_exec(
            "jcode", "run", "--json", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir or None,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            # NEVER leak the child: wait_for cancels the await, not the
            # process. An orphaned agent keeps running (burning tokens/CPU)
            # and can race the replacement runner on the same step.
            proc.kill()
            await proc.wait()
            return AgentRunResult(ok=False,
                                  error=f"jcode step timed out after {self.timeout}s (child killed)")
        out = out.decode(errors="replace")
        if proc.returncode != 0:
            return AgentRunResult(ok=False, error=err.decode()[-400:])
        try:
            return self._from_json_result(json.loads(out))
        except json.JSONDecodeError:
            start = out.find("{")
            if start >= 0:
                try:
                    return self._from_json_result(json.loads(out[start:]))
                except json.JSONDecodeError:
                    pass
            return AgentRunResult(ok=False, text=out, error="non-JSON output")


class OpencodeAdapter(BaseAdapter):
    """opencode CLI - `opencode run` headless mode (requires opencode auth)."""

    name = "opencode"
    cli = "opencode"

    AUTH_FILE = "~/.local/share/opencode/auth.json"

    def __init__(self, timeout: int = 600):
        super().__init__(timeout)
        self._auth_state: Optional[str] = None   # None=unknown, True, False

    def check(self) -> Availability:
        if not shutil.which("opencode"):
            return Availability(False, "opencode CLI not installed")
        auth = os.path.expanduser(self.AUTH_FILE)
        if not os.path.exists(auth):
            return Availability(False,
                "opencode not authenticated - run: opencode auth login")
        if self._auth_state is False:
            return Availability(False,
                "opencode credentials present but invalid (live probe failed)")
        return Availability(True, "ready")

    async def _live_probe(self) -> bool:
        """1-token round-trip to verify credentials actually work.
        Caches the verdict so a hanging/hanging-auth agent is probed once."""
        if self._auth_state is not None:
            return self._auth_state
        try:
            proc = await asyncio.create_subprocess_exec(
                "opencode", "run", "Reply with exactly: ok",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=45)
            self._auth_state = proc.returncode == 0 and bool(out.decode().strip())
        except Exception:
            self._auth_state = False
        return self._auth_state

    async def run(self, prompt: str, workdir: str = None) -> AgentRunResult:
        avail = self.check()
        if not avail.available:
            return AgentRunResult(ok=False, error=avail.reason)
        # First real run on this machine: verify credentials live before
        # trusting file existence (auth.json can exist with a dead key)
        if self._auth_state is None:
            if not await self._live_probe():
                return AgentRunResult(
                    ok=False, error="opencode auth.json present but credentials "
                                   "invalid/rejected (live probe failed)")
        try:
            return await self._run_checked(prompt, workdir)
        except Exception as e:
            return AgentRunResult(ok=False, error=f"{type(e).__name__}: {e}")
    async def _run_checked(self, prompt: str, workdir: str) -> AgentRunResult:
        proc = await asyncio.create_subprocess_exec(
            "opencode", "run", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir or None,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()  # never leak the child
            await proc.wait()
            return AgentRunResult(ok=False,
                                  error=f"opencode step timed out after {self.timeout}s (child killed)")
        if proc.returncode != 0:
            return AgentRunResult(ok=False, error=err.decode(errors="replace")[-400:])
        text = out.decode(errors="replace").strip()
        return AgentRunResult(ok=bool(text), text=text)

class ClineAdapter(BaseAdapter):
    """cline CLI headless mode."""

    name = "cline"
    cli = "cline"

    def check(self) -> Availability:
        if not shutil.which("cline"):
            return Availability(False, "cline CLI not installed")
        return Availability(True, "ready via cline CLI")

    async def _run_checked(self, prompt: str, workdir: str) -> AgentRunResult:
        proc = await asyncio.create_subprocess_exec(
            "cline", "--json", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir or None,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return AgentRunResult(ok=False,
                                  error=f"cline step timed out after {self.timeout}s (child killed)")
        out = out.decode(errors="replace")
        if proc.returncode != 0:
            return AgentRunResult(ok=False, error=err.decode()[-400:])
        try:
            return self._from_json_result(json.loads(out))
        except json.JSONDecodeError:
            start = out.find("{")
            if start >= 0:
                try:
                    return self._from_json_result(json.loads(out[start:]))
                except json.JSONDecodeError:
                    pass
            return AgentRunResult(ok=False, text=out, error="non-JSON output")


class AntigravityAdapter(BaseAdapter):
    """antigravity - reachable through jcode's antigravity provider (OAuth).

    jcode login --provider antigravity  (one-time, interactive)
    Then this adapter runs for real.
    """

    name = "antigravity"
    cli = "jcode"  # piggybacks on jcode

    def check(self) -> Availability:
        if not shutil.which("jcode"):
            run = Availability(False, "jcode CLI not installed")
            return run
        # Probe antigravity provider token presence (fast, no network)
        if not os.path.exists(os.path.expanduser(
                "~/.jcode/antigravity_oauth.json")):
            return Availability(False,
                "antigravity OAuth not configured "
                "(run: jcode login --provider antigravity)")
        return Availability(True, "ready via jcode antigravity provider")

    async def _run_checked(self, prompt: str, workdir: str) -> AgentRunResult:
        proc = await asyncio.create_subprocess_exec(
            "jcode", "run", "--json", "-p", "antigravity", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir or None,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()  # never leak the child
            await proc.wait()
            return AgentRunResult(ok=False,
                                  error=f"antigravity step timed out after {self.timeout}s (child killed)")
        out_dec = out.decode(errors="replace")
        if proc.returncode != 0:
            return AgentRunResult(ok=False, error=err.decode()[-400:])
        try:
            return self._from_json_result(json.loads(out_dec))
        except json.JSONDecodeError:
            return AgentRunResult(ok=False, text=out_dec, error="non-JSON output")


# Registry: name -> adapter instance
ADAPTERS: Dict[str, BaseAdapter] = {
    "jcode": JcodeAdapter(),
    "opencode": OpencodeAdapter(),
    "cline": ClineAdapter(),
    "antigravity": AntigravityAdapter(),
}


# ---------------------------------------------------------------------------
# Baton protocol helpers (shared by orchestrator and live tests)
# ---------------------------------------------------------------------------

def render_baton_prompt(task: str, plan: Dict, done_outcomes: List[Dict],
                        next_step_desc: str) -> str:
    """Build the relay prompt a fresh runner receives: task + baton."""
    steps_txt = "\n".join(
        f"  {s['step']}. {s['description']}" for s in plan.get("steps", []))
    done_txt = "\n".join(
        f"  - step {d['step']}: {d.get('outcome', d.get('description', ''))}"
        for d in done_outcomes)
    remaining = plan.get("remaining_steps", [])
    rem_txt = ", ".join(str(s) for s in remaining) or "none"
    return f"""You are a relay runner. A previous agent hit its token budget mid-task and handed you the baton. You have NO memory of its session except this baton.

TASK: {task}

PLAN (all steps):
{steps_txt}

COMPLETED (with outcomes - do NOT redo any of these):
{done_txt}

REMAINING STEPS: {rem_txt}
NEXT: {next_step_desc}

Your job: continue from exactly where the previous runner stopped. Complete only the remaining steps. Do not redo completed work."""


def extract_baton(text: str) -> Optional[Dict]:
    """Parse a ===BATON=== block emitted by an agent. Returns None if absent."""
    if BATON_OPEN not in text or BATON_CLOSE not in text:
        return None
    block = text.split(BATON_OPEN, 1)[1].split(BATON_CLOSE, 1)[0]
    done, plan, nxt = [], [], ""
    section = None
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("DONE:"):
            section = "done"; done.append(line[5:].strip())
        elif line.startswith("PLAN:"):
            section = "plan"; plan.append(line[5:].strip())
        elif line.startswith("NEXT:"):
            section = "next"; nxt = line[5:].strip()
        # continuation lines
        elif line and section == "done":
            done.append(line)
        elif line and section == "plan":
            plan.append(line)
    if not (done or plan):
        return None
    return {"done": done, "plan": plan, "next": nxt}


def summarize_check() -> Dict[str, Availability]:
    """Probe all adapters - used by orchestrator init and tests."""
    return {name: adapter.check() for name, adapter in ADAPTERS.items()}
