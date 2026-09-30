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
            'started_at': time.time()
        }
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
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
