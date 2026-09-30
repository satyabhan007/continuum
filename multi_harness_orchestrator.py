#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import time

STATE_FILE = ".harness_state.json"

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
            except json.JSONDecodeError:
                pass

    def save_state(self):
        with open(self.state_file, "w") as f:
            json.dump({
                "harnesses": self.harnesses,
                "active_harness": self.active_harness
            }, f, indent=4)

    def start(self, name, worktree=False, background_cmd=None):
        if name in self.harnesses:
            print(f"Harness '{name}' is already running.")
            return False

        print(f"Starting harness: {name}")
        self.harnesses[name] = {
            'state': 'running',
            'worktree': worktree,
            'background_cmd': background_cmd,
            'started_at': time.time()
        }
        self.save_state()
        return True

    def stop(self, name):
        if name not in self.harnesses:
            print(f"Harness '{name}' not found.")
            return False

        print(f"Stopping harness: {name}")
        self.harnesses.pop(name)
        if self.active_harness == name:
            self.active_harness = None
        self.save_state()
        return True

    def switch(self, name, snapshot=None):
        if name not in self.harnesses:
            print(f"Harness '{name}' not found. Please start it first.")
            return False

        if self.active_harness == name:
            print(f"Harness '{name}' is already active.")
            return True

        if self.active_harness:
            print(f"Transitioning from {self.active_harness} to {name}")
            if snapshot:
                print(f"  -> Created snapshot '{snapshot}' for {self.active_harness}")
        else:
            print(f"Switching active harness to {name}")

        self.active_harness = name
        self.save_state()
        return True

    def list_harnesses(self):
        print("Harnesses:")
        if not self.harnesses:
            print("  No harnesses running.")
            return self.harnesses

        for name, data in self.harnesses.items():
            status = data['state']
            marker = "*" if self.active_harness == name else " "
            print(f" {marker} {name} - {status}")
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
