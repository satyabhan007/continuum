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
    def start(self, name, worktree=False, background_cmd=None):
        if name in self.harnesses:
            logger.warning(f"Harness '{name}' is already running.")
            return False

        logger.info(f"Starting harness: {name}")
        self.harnesses[name] = {
            'state': 'running',
            'worktree': worktree,
            'background_cmd': background_cmd,
            'started_at': time.time(),
            'tokens_used': 0,
            'token_limit': 128000 # Default context limit (e.g., GPT-4o, Claude 3)
        }
        self.save_state()
        return True

    @benchmark
    def health_check(self):
        """Prunes harnesses that are marked as running but have no active tmux session."""
        logger.info("Running health check on active harnesses...")
        stale_harnesses = []

        for name, data in self.harnesses.items():
            # If a background_cmd was used, we expect a tmux session named hermes_<name> (or similar)
            if data.get('background_cmd'):
                try:
                    # Execute tmux has-session safely without shell=True to avoid injection
                    # We look for a session containing the name suffix, assuming 'hermes_<name>' or similar logic
                    # Using the actual session name pattern generated in the bash script
                    session_name_prefix = "hermes_"

                    # Instead of exact matching the random timestamp, we parse 'tmux ls -F "#{session_name}"'
                    output = subprocess.check_output(["tmux", "ls", "-F", "#{session_name}"], stderr=subprocess.DEVNULL, text=True)

                    # Exact and safe Python string matching
                    is_alive = False
                    for line in output.strip().split('\n'):
                        if name in line and session_name_prefix in line:
                            is_alive = True
                            break

                    if not is_alive:
                        logger.warning(f"Harness '{name}' appears dead (no tmux session found). Marking stale.")
                        stale_harnesses.append(name)

                except subprocess.CalledProcessError:
                    # Tmux server not running or no sessions at all
                    logger.warning(f"Harness '{name}' appears dead (tmux server off). Marking stale.")
                    stale_harnesses.append(name)

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
        self.harnesses.pop(name)
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
        orchestrator.start(args.name, args.worktree, args.background_cmd)
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
