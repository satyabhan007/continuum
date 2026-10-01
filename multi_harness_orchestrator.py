#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import time
import logging
import psutil
from functools import wraps

STATE_FILE = ".harness_state.json"
LOG_FILE = "orchestrator.log"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def benchmark(func):
    """Decorator to benchmark execution time and memory footprint."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        process = psutil.Process(os.getpid())
        mem_before = process.memory_info().rss
        start_time = time.perf_counter()

        try:
            result = func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error executing {func.__name__}: {e}", exc_info=True)
            raise

        end_time = time.perf_counter()
        mem_after = process.memory_info().rss

        elapsed_time_ms = (end_time - start_time) * 1000
        mem_diff_kb = (mem_after - mem_before) / 1024

        logger.info(f"[BENCHMARK] {func.__name__} | Time: {elapsed_time_ms:.3f} ms | Memory Delta: {mem_diff_kb:+.2f} KB")
        return result
    return wrapper

class MultiHarnessOrchestrator:
    def __init__(self, state_file=STATE_FILE):
        self.state_file = state_file
        self.harnesses = {}
        self.active_harness = None
        self.load_state()

    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    state = json.load(f)
                    self.harnesses = state.get("harnesses", {})
                    self.active_harness = state.get("active_harness")
                logger.debug(f"Loaded state from {self.state_file}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse state file {self.state_file}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error reading state file {self.state_file}: {e}")

    def save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({
                    "harnesses": self.harnesses,
                    "active_harness": self.active_harness
                }, f, indent=4)
            logger.debug(f"Saved state to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save state to {self.state_file}: {e}")

    @benchmark
    def start(self, name, worktree=False, background_cmd=None, agent_type="hermes"):
        if name in self.harnesses:
            logger.warning(f"Harness '{name}' is already running.")
            return False

        logger.info(f"Starting harness '{name}' using agent type: {agent_type}")

        # Determine the launch command based on agent type
        cmd = background_cmd
        if not cmd:
            if agent_type == "hermes":
                cmd = "hermes --continue"
            elif agent_type == "jcode":
                cmd = "jcode start"
            elif agent_type == "opencode":
                cmd = "opencode run"
            elif agent_type == "antigravity":
                cmd = "antigravity daemon"
            else:
                cmd = "bash" # Default fallback

        # Add worktree args if needed (simulation - usually agents handle this differently)
        if worktree and agent_type == "hermes":
            cmd = "hermes -w --continue"

        # Launch tmux session to isolate the agent
        session_name = f"{agent_type}_{name}"
        logger.info(f"Launching in tmux session: {session_name} with command: {cmd}")

        try:
            subprocess.Popen(
                ["tmux", "new-session", "-d", "-s", session_name, cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            logger.warning("tmux is not installed or not found in PATH. Proceeding without physical isolation simulation.")

        self.harnesses[name] = {
            'state': 'running',
            'agent_type': agent_type,
            'worktree': worktree,
            'background_cmd': cmd,
            'session_name': session_name,
            'started_at': time.time(),
            'tokens_used': 0,
            'token_limit': 128000 # Default context limit
        }
        self.save_state()
        return True

    @benchmark
    def health_check(self):
        """Prunes harnesses that are marked as running but have no active tmux session."""
        logger.info("Running health check on active harnesses...")
        stale_harnesses = []

        for name, data in self.harnesses.items():
            session_name = data.get('session_name')

            if session_name:
                try:
                    # Parse 'tmux ls -F "#{session_name}"' safely
                    output = subprocess.check_output(["tmux", "ls", "-F", "#{session_name}"], stderr=subprocess.DEVNULL, text=True)

                    is_alive = False
                    for line in output.strip().split('\n'):
                        if line.strip() == session_name:
                            is_alive = True
                            break

                    if not is_alive:
                        logger.warning(f"Harness '{name}' appears dead (tmux session '{session_name}' not found). Marking stale.")
                        stale_harnesses.append(name)

                except subprocess.CalledProcessError:
                    logger.warning(f"Harness '{name}' appears dead (tmux server off or no sessions). Marking stale.")
                    stale_harnesses.append(name)
                except FileNotFoundError:
                    logger.warning("tmux is not installed. Skipping health check.")
                    break

        for name in stale_harnesses:
            self.harnesses[name]['state'] = 'stale'

        if stale_harnesses:
            self.save_state()

        return stale_harnesses

    @benchmark
    def update_context(self, name, tokens_used):
        """Updates the token usage for a harness and alerts if it exceeds the limit."""
        if name not in self.harnesses:
            logger.warning(f"Harness '{name}' not found.")
            return False

        try:
            tokens = int(tokens_used)
        except ValueError:
            logger.error("Tokens used must be an integer.")
            return False

        self.harnesses[name]['tokens_used'] = tokens
        limit = self.harnesses[name].get('token_limit', 128000)

        if tokens >= limit:
            logger.error(f"ALERT: Harness '{name}' has exceeded its context window limit ({tokens}/{limit} tokens).")
            self.harnesses[name]['state'] = 'context_exceeded'
        elif tokens >= limit * 0.9:
            logger.warning(f"WARNING: Harness '{name}' is approaching its context window limit ({tokens}/{limit} tokens).")

        self.save_state()
        return True

    @benchmark
    def stop(self, name):
        if name not in self.harnesses:
            logger.warning(f"Harness '{name}' not found.")
            return False

        logger.info(f"Stopping harness: {name}")
        data = self.harnesses.pop(name)

        # Kill the associated tmux session
        session_name = data.get('session_name')
        if session_name:
            try:
                subprocess.Popen(
                    ["tmux", "kill-session", "-t", session_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except FileNotFoundError:
                pass

        if self.active_harness == name:
            self.active_harness = None
            logger.info(f"Cleared active harness since '{name}' was stopped.")
        self.save_state()
        return True

    @benchmark
    def switch(self, name, snapshot=None):
        if name not in self.harnesses:
            logger.warning(f"Harness '{name}' not found. Please start it first.")
            return False

        if self.active_harness == name:
            logger.info(f"Harness '{name}' is already active.")
            return True

        if self.active_harness:
            logger.info(f"Transitioning from {self.active_harness} to {name}")
            if snapshot:
                logger.info(f"Created snapshot '{snapshot}' for {self.active_harness}")
        else:
            logger.info(f"Switching active harness to {name}")

        self.active_harness = name
        self.save_state()
        return True

    @benchmark
    def list_harnesses(self):
        logger.info("Listing harnesses:")
        if not self.harnesses:
            logger.info("  No harnesses running.")
            return self.harnesses

        for name, data in self.harnesses.items():
            status = data['state']
            marker = "*" if self.active_harness == name else " "
            logger.info(f" {marker} {name} - {status}")
        return self.harnesses

    def get_state(self):
        return {
            "harnesses": self.harnesses,
            "active_harness": self.active_harness
        }

def main():
    parser = argparse.ArgumentParser(description="Multi-Harness Orchestrator for Hermes")
    subparsers = parser.add_subparsers(dest="command")

    # Start command
    start_parser = subparsers.add_parser("start", help="Start a new harness")
    start_parser.add_argument("name", help="Name of the harness")
    start_parser.add_argument("--worktree", action="store_true", help="Use git worktree")
    start_parser.add_argument("--background-cmd", help="Command to run in background")
    start_parser.add_argument("--agent-type", default="hermes", choices=["hermes", "jcode", "opencode", "antigravity", "custom"], help="The type of agent harness to use")

    # Stop command
    stop_parser = subparsers.add_parser("stop", help="Stop a harness")
    stop_parser.add_argument("name", help="Name of the harness")

    # Switch command
    switch_parser = subparsers.add_parser("switch", help="Switch active harness")
    switch_parser.add_argument("name", help="Name of the harness to switch to")
    switch_parser.add_argument("--snapshot", help="Snapshot label before switching")

    # List command
    subparsers.add_parser("list", help="List all harnesses")

    # Health check command
    subparsers.add_parser("health", help="Run a health check on active harnesses")

    # Update context command
    context_parser = subparsers.add_parser("update-context", help="Update token usage for a harness")
    context_parser.add_argument("name", help="Name of the harness")
    context_parser.add_argument("tokens", help="Number of tokens used")

    args = parser.parse_args()
    orchestrator = MultiHarnessOrchestrator()

    if args.command == "start":
        orchestrator.start(args.name, args.worktree, args.background_cmd, args.agent_type)
    elif args.command == "stop":
        orchestrator.stop(args.name)
    elif args.command == "switch":
        orchestrator.switch(args.name, args.snapshot)
    elif args.command == "list":
        orchestrator.list_harnesses()
    elif args.command == "health":
        orchestrator.health_check()
    elif args.command == "update-context":
        orchestrator.update_context(args.name, args.tokens)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
