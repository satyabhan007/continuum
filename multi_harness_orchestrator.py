#!/usr/bin/env python3
"""
Standalone Multi-Harness Orchestrator - Simplified Version

A simplified version of the Multi-Harness Orchestrator that demonstrates
the core orchestration logic without external dependencies.
"""

import asyncio
import json
import os
import time
import urllib.request
import uuid
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import logging
from pathlib import Path

log = logging.getLogger(__name__)

class HarnessStatus(Enum):
    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    MAINTENANCE = "maintenance"
    ERROR = "error"

@dataclass
class TokenBudget:
    total_budget: int
    used_tokens: int = 0
    remaining_tokens: int = field(init=False)
    
    def __post_init__(self):
        self.remaining_tokens = self.total_budget - self.used_tokens

@dataclass
class AgentContext:
    harness_name: str
    agent_id: str
    session_id: str
    task: str
    execution_state: Dict[str, Any]
    conversation_history: List[Dict[str, Any]]
    step_history: List[Dict[str, Any]]
    token_usage: Dict[str, Any]
    current_step: int = 0
    max_steps: int = 10
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)

class MultiHarnessOrchestrator:
    """
    Simplified orchestrator for managing multiple agent harnesses with token monitoring and context preservation.
    
    This version demonstrates the core orchestration logic without external dependencies,
    focusing on:
    - Harness coordination
    - Token management
    - Context preservation
    - Intelligent handoff mechanisms
    """
    
    def __init__(self, config_path: str = "multi_harness_config.json"):
        # Config can live at repo root or under config/; prefer an existing file
        if not os.path.exists(config_path):
            alt = os.path.join("config", os.path.basename(config_path))
            if os.path.exists(alt):
                config_path = alt
        self.config = self.load_config(config_path)
        self.harnesses = {}
        self.agent_contexts = {}
        self.token_monitor = TokenMonitor(self.config['handoff_strategy'])
        self.context_persistence = ContextPersistence()
        
        self.initialize_harnesses()
        log.info("Multi-Harness Orchestrator (Simplified) initialized successfully")
    
    def load_config(self, config_path: str) -> Dict:
        """Load multi-harness configuration with default values"""
        default_config = {
            "harness_pool": [
                {
                    "name": "opencode",
                    "models": ["gpt-5.5-pro", "claude-opus-5"],
                    "token_budget": 100000,
                    "default_model": "gpt-5.5-pro",
                    "cost_per_1k_tokens": 0.002,
                    "max_concurrent_tasks": 3,
                    "api_endpoint": "https://api.openai.com/v1"
                },
                {
                    "name": "cline",
                    "models": ["claude-sonnet-5", "gpt-4o"],
                    "token_budget": 80000,
                    "default_model": "claude-sonnet-5",
                    "cost_per_1k_tokens": 0.003,
                    "max_concurrent_tasks": 2,
                    "api_endpoint": "https://api.anthropic.com/v1"
                },
                {
                    "name": "antigravity",
                    "models": ["gemini-2.5-pro", "gpt-5.6-sol"],
                    "token_budget": 60000,
                    "default_model": "gemini-2.5-pro",
                    "cost_per_1k_tokens": 0.004,
                    "max_concurrent_tasks": 1,
                    "api_endpoint": "https://generativelanguage.googleapis.com/v1"
                }
            ],
            "handoff_strategy": {
                "mode": "token_based",
                "context_preservation": True,
                "auto_recovery": True,
                "token_threshold_percent": 80,
                "graceful_transfer": True
            },
            "conversation_persistence": {
                "enabled": True,
                "storage": "swarm_artifacts",
                "compaction_threshold": 0.8,
                "max_history_size": 10000
            },
            "execution_settings": {
                "max_execution_time_seconds": 300,
                "retry_attempts": 3,
                "backoff_factor": 1.5,
                "enable_streaming": True
            }
        }
        
        try:
            with open(config_path, 'r') as f:
                return {**default_config, **json.load(f)}
        except (FileNotFoundError, json.JSONDecodeError):
            return default_config
    
    def initialize_harnesses(self):
        """Initialize all configured harnesses"""
        for harness_config in self.config['harness_pool']:
            harness_name = harness_config['name']
            self.harnesses[harness_name] = {
                'config': harness_config,
                'status': HarnessStatus.ACTIVE,
                'active_agents': {},
                'token_budget': TokenBudget(
                    total_budget=harness_config['token_budget']
                ),
                'execution_queue': asyncio.Queue(),
                'task_counter': 0
            }
            log.info(f"Initialized harness: {harness_name}")
    
    async def execute_task(self, task: str, preferences: Optional[Dict] = None) -> Dict:
        """Execute a task with automatic harness coordination and handoff"""
        
        # Create agent context for this execution
        execution_id = f"exec_{int(time.time())}_{str(uuid.uuid4())[:8]}"
        
        # Find best harness based on task complexity and preferences
        target_harness = self.select_harness(task, preferences)
        
        # The baton: task + what's done + what's planned. A new runner must
        # be able to resume from this alone, so the plan carries semantic
        # step descriptions, not just step numbers.
        max_steps = self.config['execution_settings']['max_execution_time_seconds'] // 60
        step_plan = [
            {'step': i + 1, 'description': desc}
            for i, desc in enumerate(self._derive_step_plan(task, max_steps))
        ]
        execution_state = {
            'status': 'starting',
            'execution_id': execution_id,
            'plan': {
                'goal': task,
                'total_steps': max_steps,
                'steps': step_plan,
                'completed_steps': [],
                'remaining_steps': [s['step'] for s in step_plan],
            },
        }
        
        context = AgentContext(
            harness_name=target_harness,
            agent_id=f"agent_{execution_id}",
            session_id=f"session_{execution_id}",
            task=task,
            execution_state=execution_state,
            conversation_history=[],
            step_history=[],
            token_usage={},
            current_step=0,
            max_steps=max_steps
        )
        
        self.agent_contexts[execution_id] = context
        
        try:
            # Execute task with harness coordination
            result = await self.execute_with_harness_coordination(
                execution_id, context, preferences
            )
            
            # Persist successful execution context
            await self.context_persistence.save_context(
                f"completed_{execution_id}", 
                context.__dict__
            )
            
            return {
                'success': True,
                'execution_id': execution_id,
                'result': result,
                'harness_used': target_harness,
                'tokens_consumed': context.token_usage.get('total_tokens', 0),
                'execution_time': time.time() - context.created_at
            }
            
        except Exception as e:
            log.error(f"Task execution failed for {execution_id}: {str(e)}")
            
            # Save failed execution context for recovery
            await self.context_persistence.save_context(
                f"failed_{execution_id}", 
                context.__dict__
            )
            
            return {
                'success': False,
                'execution_id': execution_id,
                'error': str(e),
                'harness_used': target_harness,
                'tokens_consumed': context.token_usage.get('total_tokens', 0),
                'can_recover': True
            }
    
    async def execute_with_harness_coordination(self, execution_id: str, context: AgentContext,
                                               preferences: Optional[Dict] = None) -> Any:
        """Execute task with token monitoring and automatic handoff.

        Relay semantics: if the current harness runs out of tokens mid-run,
        the baton (context: task, plan, history, state) is passed to the next
        runner and execution resumes from the last completed step - the task
        is NEVER restarted from scratch.
        """

        harness_name = context.harness_name
        harness = self.harnesses[harness_name]

        # Monitor token consumption during execution
        token_monitor = self.token_monitor.start_monitoring(
            execution_id,
            harness['token_budget']
        )

        try:
            # Relay loop: run one step at a time, checking the baton-carrier's
            # budget between steps so exhaustion mid-task triggers a handoff
            # instead of silently overshooting or dying.
            while True:
                # Check exhaustion before each step (fast fail)
                if harness['token_budget'].remaining_tokens <= 0:
                    raise TokenExhaustedError(f"Harness {harness_name} token budget exhausted")

                # Proactive threshold: pass the baton BEFORE exhaustion.
                threshold = self.config['handoff_strategy'].get('token_threshold_percent', 80) / 100.0
                budget_ratio = harness['token_budget'].remaining_tokens / max(harness['token_budget'].total_budget, 1)
                if budget_ratio <= (1 - threshold) and self.find_next_available_harness(harness_name):
                    log.info(f"Harness {harness_name} below {threshold:.0%} budget "
                             f"({budget_ratio:.0%} left) - proactive handoff")
                    raise TokenExhaustedError(
                        f"Harness {harness_name} crossed {int((1-threshold)*100)}% budget threshold"
                    )

                # Execute ONE step of the task on this harness
                result = await self._simulate_task_execution(
                    execution_id, context, harness, preferences
                )

                if result['status'] == 'completed':
                    await token_monitor.stop_monitoring()
                    return result
        except (TokenExhaustedError, HarnessError) as e:
            # Trigger handoff if not already in handoff process.
            # NOTE: execution_state is a dict - check with .get(), not getattr
            if not context.execution_state.get('handoff_triggered', False):
                log.info(f"Triggering handoff for {execution_id}: {str(e)}")
                # Jev gates the handoff: is the baton (context) sufficient for
                # another runner to continue, or do we need a human?
                jev_engine = self._get_jev_engine()
                if jev_engine:
                    readiness = jev_engine.is_context_relay_ready(context)
                    if readiness is False:
                        log.warning(f"Jev flagged context as NOT relay-ready for {execution_id}: "
                                     "escalating instead of dropping the baton")
                        raise HarnessError(
                            f"Context not relay-ready for {execution_id}; manual intervention needed"
                        ) from e
                    elif readiness is None:
                        log.info("Jev unavailable/uncertain - proceeding with deterministic handoff")
                await self.trigger_harness_handoff(execution_id, context, str(e), task_hint=context.task)

                if context.execution_state.get('handoff_failed'):
                    raise HarnessError(f"Handoff failed for {execution_id}: no harness available") from e

                # Resume on the new harness from the last completed step.
                # handoff_triggered stays True so a second exhaustion inside
                # the same relay leg re-enters this path instead of recursing
                # from the top and redoing finished steps.
                context.execution_state['handoff_resumed'] = True
                context.execution_state['handoff_triggered'] = False
                return await self.execute_with_harness_coordination(
                    execution_id, context, preferences
                )
            else:
                raise
    
    async def _simulate_task_execution(self, execution_id: str, context: AgentContext, 
                                      harness: Dict, preferences: Optional[Dict] = None) -> Any:
        """Simulate task execution with a specific harness"""
        
        # Simulate agent execution - ONE step of the task
        await asyncio.sleep(0.05)  # Simulate processing time

        context.current_step += 1
        context.last_updated = time.time()

        # Keep the plan in the baton current: move this step from
        # remaining to completed so a new runner knows exactly where to resume
        plan = context.execution_state.setdefault('plan', {
            'goal': context.task, 'total_steps': context.max_steps,
            'steps': [], 'completed_steps': [], 'remaining_steps': [],
        })
        if context.current_step not in plan['completed_steps']:
            plan['completed_steps'].append(context.current_step)
        plan['remaining_steps'] = [s for s in range(1, plan['total_steps'] + 1)
                                    if s not in plan['completed_steps']]
        context.execution_state['status'] = 'running'
        context.execution_state['next_step'] = (
            plan['remaining_steps'][0] if plan['remaining_steps'] else None)

        # Record what this step actually accomplished, with the semantic
        # description from the plan. A new runner reading the baton must be
        # able to see not just THAT a step ran, but WHAT it did and found.
        step_desc = next((s['description'] for s in plan.get('steps', [])
                          if s['step'] == context.current_step),
                         f"Step {context.current_step} of {context.task}")
        step_outcome = (
            f"Completed: {step_desc}. "
            f"Findings: work proceeded as planned on "
            f"{harness['config']['name']}; no blockers encountered; "
            f"next runner should pick up at step "
            f"{(plan['remaining_steps'][0] if plan['remaining_steps'] else 'completion')}."
        )

        # A step completes the task only when it is the final planned step;
        # otherwise the task stays in progress so the relay loop continues
        # and budget checks run between steps.
        is_final_step = context.current_step >= context.max_steps

        # Generate a step result
        result = {
            'status': 'completed' if is_final_step else 'in_progress',
            'output': (f"Step {context.current_step}/{context.max_steps} done on "
                       f"{harness['config']['name']} for: {context.task}"),
            'harness': harness['config']['name'],
            'execution_id': execution_id,
            'step': context.current_step,
            'timestamp': time.time()
        }
        
        # Update context with execution results
        context.conversation_history.append({
            'timestamp': time.time(),
            'input': f"Execute plan step {context.current_step}",
            'output': step_outcome,
            'harness': harness['config']['name']
        })
        
        context.step_history.append({
            'step': context.current_step,
            'status': 'completed',
            'description': step_desc,
            'outcome': step_outcome,
            'harness': harness['config']['name']
        })
        
        # Update token usage (simulate based on execution)
        tokens_used = self._estimate_tokens_used(result)
        context.token_usage['total_tokens'] = tokens_used
        harness['token_budget'].used_tokens += tokens_used
        harness['token_budget'].remaining_tokens -= tokens_used
        
        context.current_step += 1
        context.last_updated = time.time()
        
        return result
    
    def _derive_step_plan(self, task: str, max_steps: int) -> List[str]:
        """Derive semantic step descriptions for the task plan.

        A real deployment would let the planning agent write these; for the
        simulation we generate standard software-task phases so the baton
        carries genuine 'what is planned' content, not bare step numbers.
        """
        phases = [
            f"Analyze the requirements and current state for: {task}",
            f"Design the approach and break down changes for: {task}",
            f"Implement the core changes for: {task}",
            f"Test and verify the changes for: {task}",
            f"Review, document, and wrap up: {task}",
        ]
        if max_steps <= len(phases):
            return phases[:max_steps]
        # Pad by reusing implementation phases with sequence numbers
        plan = list(phases)
        for i in range(len(phases), max_steps):
            plan.append(f"Continue implementation phase {i - 1} for: {task}")
        return plan

    def select_harness(self, task: str, preferences: Optional[Dict] = None) -> str:
        """Select the best harness for a given task"""
        
        if preferences and 'preferred_harness' in preferences:
            preferred = preferences['preferred_harness']
            if self.is_harness_available(preferred):
                return preferred
        
        # Task-based harness selection
        task_complexity = self._estimate_task_complexity(task)
        
        # Sort harnesses by availability and suitability
        available_harnesses = []
        for harness_name, harness_info in self.harnesses.items():
            if harness_info['status'] == HarnessStatus.ACTIVE:
                budget_efficiency = harness_info['token_budget'].remaining_tokens / harness_info['token_budget'].total_budget
                available_harnesses.append((harness_name, budget_efficiency, task_complexity))
        
        # Select best harness
        if available_harnesses:
            available_harnesses.sort(key=lambda x: x[1], reverse=True)
            return available_harnesses[0][0]
        
        # Fallback to first active harness
        for harness_name, harness_info in self.harnesses.items():
            if harness_info['status'] == HarnessStatus.ACTIVE:
                return harness_name
        
        raise NoAvailableHarnessError("No active agent harnesses available")
    
    async def trigger_harness_handoff(self, execution_id: str, context: AgentContext,
                                    reason: str, task_hint: Optional[str] = None) -> None:
        """Trigger automatic handoff to another harness"""
        
        # Mark that handoff has been triggered
        context.execution_state['handoff_triggered'] = True
        context.execution_state['handoff_reason'] = reason
        context.execution_state['handoff_timestamp'] = time.time()
        
        # Find next available harness (Jev-informed when possible)
        next_harness = self.find_next_available_harness(context.harness_name, task=task_hint)
        
        if next_harness:
            # Transfer context to new harness
            await self.transfer_context_to_harness(
                context.harness_name, next_harness, context, execution_id
            )
            
            # Update context
            context.harness_name = next_harness
            context.execution_state['handoff_completed'] = True
            context.execution_state['new_harness'] = next_harness
            
            log.info(f"Handoff completed for {execution_id}: {context.harness_name}")
        else:
            log.error(f"No available harness for handoff of {execution_id}")
            context.execution_state['handoff_failed'] = True
    
    def find_next_available_harness(self, current_harness: str,
                                    task: Optional[str] = None) -> Optional[str]:
        """Find the next available harness for handoff.

        Two-tier selection:
        1. If a task description and the Jev decision engine are available,
           Jev scores each candidate harness for suitability given the task
           and remaining budgets (typed judgment, not just max-budget).
        2. Fallback: deterministic max-remaining-budget selection.
        """

        candidates = []
        for harness_name, harness_info in self.harnesses.items():
            if harness_name != current_harness and harness_info['status'] == HarnessStatus.ACTIVE:
                budget_ratio = harness_info['token_budget'].remaining_tokens / max(harness_info['token_budget'].total_budget, 1)
                if harness_info['token_budget'].remaining_tokens > 0:
                    candidates.append((harness_name, budget_ratio, harness_info))

        if not candidates:
            return None

        # Try Jev-based intelligent selection first
        if task:
            jev_engine = self._get_jev_engine()
            if jev_engine:
                choice = jev_engine.select_harness_for_task(task, candidates)
                if choice:
                    return choice

        # Deterministic fallback: most remaining budget wins
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    def _get_jev_engine(self):
        """Lazily create the Jev decision engine (None if unavailable)"""
        if getattr(self, '_jev_engine_created', False):
            return self._jev_engine
        self._jev_engine_created = True
        self._jev_engine = JevDecisionEngine()
        return self._jev_engine
    
    async def transfer_context_to_harness(self, from_harness: str, to_harness: str,
                                        context: AgentContext, execution_id: str) -> None:
        """Transfer execution context to another harness - pass the baton"""

        # Save current context (use execution_id parameter - AgentContext has
        # no execution_id attribute of its own; it lives in execution_state)
        await self.context_persistence.save_context(
            f"{from_harness}_handoff_{execution_id}",
            {
                'execution_id': execution_id,
                'harness_name': from_harness,
                'agent_id': context.agent_id,
                'session_id': context.session_id,
                'task': context.task,
                'execution_state': context.execution_state,
                'conversation_history': context.conversation_history,
                'step_history': context.step_history,
                'token_usage': context.token_usage,
                'current_step': context.current_step,
                'max_steps': context.max_steps,
                'created_at': context.created_at,
                'last_updated': context.last_updated,
                'handoff_timestamp': time.time(),
                'handoff_reason': context.execution_state.get('handoff_reason', 'unknown')
            }
        )
        
        # Load or create context in new harness
        saved_context = await self.context_persistence.load_context(
            f"{from_harness}_handoff_{execution_id}"
        )
        
        if saved_context:
            # Update current context with saved data
            for key, value in saved_context.items():
                if hasattr(context, key) and key != 'handoff_timestamp':
                    setattr(context, key, value)

        # Update harness execution queue
        target_harness = self.harnesses[to_harness]
        await target_harness['execution_queue'].put({
            'execution_id': execution_id,
            'context': context.__dict__,
            'timestamp': time.time()
        })

        log.info(f"Context transferred from {from_harness} to {to_harness} for {execution_id}")
    
    def is_harness_available(self, harness_name: str) -> bool:
        """Check if a harness is available"""
        return (harness_name in self.harnesses and 
                self.harnesses[harness_name]['status'] == HarnessStatus.ACTIVE and
                self.harnesses[harness_name]['token_budget'].remaining_tokens > 0)
    
    def _estimate_task_complexity(self, task: str) -> float:
        """Estimate task complexity for harness selection"""
        complexity_indicators = [
            'complex', 'advanced', 'difficult', 'challenging', 'multi-step',
            'requires analysis', 'synthesis', 'comparison', 'evaluation',
            'machine learning', 'deep learning', 'neural networks',
            'transformers', 'large scale'
        ]
        
        base_complexity = 1.0
        complexity_score = base_complexity
        
        for indicator in complexity_indicators:
            if indicator.lower() in task.lower():
                complexity_score *= 1.5
        
        return min(complexity_score, 5.0)
    
    def _estimate_tokens_used(self, result: Any) -> int:
        """Estimate token usage based on result size"""
        if isinstance(result, str):
            # Simple heuristic for text
            words = len(result.split())
            return int(words * 1.3)
        else:
            # Default estimate for structured results
            return 500
    
    def generate_random_id(self, length: int = 8) -> str:
        """Generate a random ID"""
        import random
        import string
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

class JevDecisionEngine:
    """Typed decision engine backed by TypeSafe's Jev (System One).

    The orchestrator's judgment calls - which harness should take the baton,
    whether the context is sufficient to continue - become calibrated typed
    decisions instead of hard-coded threshold logic. Jev is a decision model,
    NOT a text generator, so it never rewrites the baton; it only judges.

    Failure philosophy: if Jev is unreachable or uncertain, return None and
    let the orchestrator fall back to its deterministic rules. The relay
    never stops because a judgment service was down.
    """

    API_URL = "https://api.typesafe.ai/v1/systemone"
    MODEL = "jev-latest"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 10.0,
                 confidence_gate: float = 0.6):
        self.api_key = api_key or self._resolve_key()
        self.timeout = timeout
        self.confidence_gate = confidence_gate

    @staticmethod
    def _resolve_key() -> Optional[str]:
        key = os.environ.get("TYPESAFE_API_KEY")
        if key:
            return key
        # Platform-standard config locations:
        #   Linux/macOS: ~/.config/typesafe/apikey  (XDG convention)
        #   Windows:     %APPDATA%/typesafe/apikey  (Roaming convention)
        candidates = [os.path.expanduser("~/.config/typesafe/apikey")]
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(os.path.join(appdata, "typesafe", "apikey"))
        for path in candidates:
            if os.path.exists(path):
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("export TYPESAFE_API_KEY="):
                            return line.split("=", 1)[1].strip().strip('"')
        return None

    def _ask(self, state, questions) -> Optional[Dict]:
        """POST to System One; return answers dict, or None on any failure"""
        if not self.api_key:
            return None
        try:
            body = json.dumps({"state": state, "model": self.MODEL,
                               "questions": questions}).encode()
            req = urllib.request.Request(
                self.API_URL, data=body, method="POST",
                headers={"Authorization": "Bearer " + self.api_key,
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())["answers"]
        except Exception as e:
            log.debug(f"Jev decision call failed (falling back to deterministic): {e}")
            return None

    def select_harness_for_task(self, task: str, candidates: List[Tuple[str, float, Dict]]) -> Optional[str]:
        """Choose the relay runner best suited to the task.

        Jev judges each candidate harness: fit of its models to the task,
        given remaining budget and concurrency. Returns None when Jev is
        unavailable or below the confidence gate, deferring to fallback.
        """
        if not candidates:
            return None
        criteria = {}
        for name, ratio, info in candidates:
            # info may be a full harness dict (has 'config') or a bare dict;
            # never crash on shape - degrade gracefully to budget-only info
            cfg = (info or {}).get('config', {}) if isinstance(info, dict) else {}
            criteria[name] = {
                "models": ", ".join(cfg.get('models', [])),
                "budget_remaining_percent": round(ratio * 100),
                "max_concurrent_tasks": cfg.get('max_concurrent_tasks', 1),
            }
        state = {
            "task": task[:1000],
            "current_runner_exhausted": True,
            "candidate_harnesses": criteria,
        }
        questions = {
            "pick_runner": {
                "type": "choice",
                "instructions": (
                    "In an agent relay, the current runner ran out of tokens. "
                    "Which candidate harness should take the baton and continue "
                    "this task? Judge fit between the task and each harness's "
                    "models, budget headroom, and concurrency capacity."
                ),
                "criteria": {
                    name: (
                        f"Models: {rubric['models']}. Budget remaining: "
                        f"{rubric['budget_remaining_percent']}%. Concurrency: "
                        f"{rubric['max_concurrent_tasks']} task(s)."
                    )
                    for name, rubric in criteria.items()
                },
            }
        }
        answers = self._ask(state, questions)
        if not answers:
            return None
        a = answers.get("pick_runner", {})
        if a.get("confidence", 0) >= self.confidence_gate and a.get("choice"):
            return a["choice"]
        return None

    def is_context_relay_ready(self, context: 'AgentContext') -> Optional[bool]:
        """Gate: can a new runner continue from this baton (context)?

        Returns True  - context is relay-ready, proceed with handoff.
        Returns False - context is missing critical state; escalate to human.
        Returns None  - Jev unavailable/uncertain; use deterministic handoff.
        """
        # Fast structural check first (free, no API call)
        has_task = bool(context and getattr(context, 'task', ''))
        has_history = bool(getattr(context, 'step_history', None)) or bool(getattr(context, 'conversation_history', None))
        has_state = bool(getattr(context, 'execution_state', None))
        if not (has_task and has_state):
            return False
        if not has_history:
            return False  # no record of what was done; a new runner would redo work

        questions = {
            "relay_ready": {
                "type": "noul",
                "instructions": {
                    "question": (
                        "An agent runner hit its token limit mid-task and must hand "
                        "the work to a new runner. Looking at `state`, can the new "
                        "runner continue the task from where the previous one "
                        "stopped, without redoing completed work and without "
                        "missing the plan or current state?"
                    ),
                    "checklist": (
                        "Judge against ALL of: (1) the task/goal is stated; (2) the "
                        "plan in `state.execution_state.plan` shows total steps, "
                        "each step's description, which are completed, and which "
                        "remain; (3) `state.steps_completed` and "
                        "`state.last_step_outcomes` show what was actually done "
                        "with outcomes; (4) `state.execution_state.next_step` "
                        "names where to resume. Absence of ANY of these makes "
                        "the context NOT sufficient for resumption."
                    ),
                },
                "criteria": {
                    "true": ("All of: task stated, plan with step descriptions and "
                            "completed/remaining lists present, step outcomes "
                            "recorded, and resume point named - a new runner can "
                            "pick up exactly where this one stopped"),
                    "false": ("Missing any of: the task, the plan's step "
                             "descriptions or progress lists, step outcomes, or "
                             "the resume point - a new runner could not resume "
                             "reliably and would redo or guess"),
                },
            }
        }
        state = {
            "task": context.task[:1000],
            "execution_state": {
                k: v for k, v in context.execution_state.items()
                if k not in ('conversation_history', 'step_history')
            },
            "steps_completed": len(context.step_history),
            "conversations_logged": len(context.conversation_history),
            "handoff_reason": context.execution_state.get('handoff_reason')
                              or "token budget exhausted before task completion",
            "last_step_outcomes": [
                {"step": s["step"], "description": s.get("description", ""),
                 "outcome": s.get("outcome", "")}
                for s in (context.step_history[-3:] if context.step_history else [])
            ],
        }
        answers = self._ask(state, questions)
        if not answers:
            return None
        noul = answers.get("relay_ready", {}).get("noul")
        if noul is None:
            return None
        return noul >= 0.5


class TokenMonitor:
    """Monitor token consumption during execution"""
    
    def __init__(self, handoff_config: Dict):
        self.handoff_config = handoff_config
        self.active_monitoring = {}
    
    def start_monitoring(self, execution_id: str, token_budget: TokenBudget) -> 'TokenMonitorHandle':
        """Start monitoring token consumption"""
        return TokenMonitorHandle(self, execution_id, token_budget)
    
    def stop_monitoring(self, execution_id: str) -> None:
        """Stop monitoring"""
        if execution_id in self.active_monitoring:
            del self.active_monitoring[execution_id]

class TokenMonitorHandle:
    """Handle for token monitoring"""
    
    def __init__(self, monitor: TokenMonitor, execution_id: str, token_budget: TokenBudget):
        self.monitor = monitor
        self.execution_id = execution_id
        self.token_budget = token_budget
        self.start_time = time.time()
        self.tokens_tracked = 0
    
    def track_token_usage(self, tokens: int) -> None:
        """Track token usage"""
        self.tokens_tracked += tokens
        if self.execution_id in self.monitor.active_monitoring:
            self.monitor.active_monitoring[self.execution_id]['total_tokens'] = self.tokens_tracked
    
    async def stop_monitoring(self) -> Dict:
        """Stop monitoring and return statistics"""
        end_time = time.time()
        duration = end_time - self.start_time
        
        stats = {
            'execution_id': self.execution_id,
            'tokens_tracked': self.tokens_tracked,
            'duration_seconds': duration,
            'tokens_per_second': self.tokens_tracked / duration if duration > 0 else 0,
            'timestamp': end_time
        }
        
        self.monitor.stop_monitoring(self.execution_id)
        return stats

class ContextPersistence:
    """Persistent storage for execution contexts"""
    
    def __init__(self):
        self.context_store = {}
    
    async def save_context(self, key: str, context_data: Dict) -> None:
        """Save context to persistent storage"""
        self.context_store[key] = {
            **context_data,
            'saved_at': time.time()
        }
        log.debug(f"Context saved: {key}")
    
    async def load_context(self, key: str) -> Optional[Dict]:
        """Load context from persistent storage"""
        return self.context_store.get(key)
    
    async def cleanup_old_contexts(self, max_age_seconds: int = 3600) -> None:
        """Clean up old contexts"""
        current_time = time.time()
        keys_to_delete = []
        
        for key, context in self.context_store.items():
            if current_time - context['saved_at'] > max_age_seconds:
                keys_to_delete.append(key)
        
        for key in keys_to_delete:
            del self.context_store[key]
        
        if keys_to_delete:
            log.info(f"Cleaned up {len(keys_to_delete)} old contexts")

# Custom exceptions
class TokenExhaustedError(Exception):
    pass

class HarnessError(Exception):
    pass

class NoAvailableHarnessError(Exception):
    pass

# CLI interface
async def main():
    """Main entry point for the Multi-Harness Orchestrator"""
    
    # Initialize logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Initialize orchestrator
    orchestrator = MultiHarnessOrchestrator()
    
    print("Multi-Harness Orchestrator - Ready (Simplified Version)")
    print("Available commands:")
    print("  execute <task> - Execute a task with automatic harness coordination")
    print("  status - Show harness status and token budgets")
    print("  handoff <execution_id> - Trigger handoff for execution")
    print("  exit - Exit the orchestrator")
    
    try:
        while True:
            command = input("\n> ").strip()
            
            if command.startswith('execute '):
                task = command[8:]
                print(f"Executing task: {task}")
                result = await orchestrator.execute_task(task)
                print(f"Result: {result}")
                
            elif command == 'status':
                print("\nHarness Status:")
                for harness_name, harness_info in orchestrator.harnesses.items():
                    budget = harness_info['token_budget']
                    print(f"  {harness_name}: {harness_info['status'].value} - "
                          f"Tokens: {budget.used_tokens}/{budget.total_budget} ({budget.remaining_tokens} remaining)")
                
            elif command.startswith('handoff '):
                execution_id = command[8:]
                print(f"Triggering handoff for {execution_id}")
                # Handoff logic would go here
                
            elif command == 'exit':
                print("Exiting Multi-Harness Orchestrator")
                break
                
            else:
                print(f"Unknown command: {command}")
    
    except KeyboardInterrupt:
        print("\nShutting down Multi-Harness Orchestrator")
    
    except Exception as e:
        log.error(f"Orchestrator error: {str(e)}")
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())

# Export for import
__all__ = [
    'MultiHarnessOrchestrator',
    'TokenBudget',
    'AgentContext',
    'TokenMonitor',
    'ContextPersistence',
    'JevDecisionEngine'
]
