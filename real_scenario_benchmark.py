#!/usr/bin/env python3
"""
Real-Scenario Use Case & Benchmark Script
-----------------------------------------
This script simulates a heavy, real-world multi-agent workflow using the
Continuum Multi-Harness Orchestrator. It spins up various agent types,
simulates context usage, performs LangGraph sweeps, and then parses
the orchestrator.log to provide a summarized benchmark report.
"""

import time
import os
import re
from multi_harness_orchestrator import MultiHarnessOrchestrator, LOG_FILE, STATE_FILE

def clean_state():
    """Ensure a clean slate before benchmarking."""
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)

def run_scenario():
    print("==================================================")
    print("🚀 Starting Real-Scenario Multi-Agent Benchmark")
    print("==================================================")

    clean_state()
    orchestrator = MultiHarnessOrchestrator()

    print("\n[1] Initializing Agent Nodes...")
    orchestrator.start("manager-ai", agent_type="hermes")
    orchestrator.start("backend-api", agent_type="jcode", worktree=True)
    orchestrator.start("frontend-ui", agent_type="opencode")
    orchestrator.start("physics-sim", agent_type="antigravity")

    print("\n[2] Simulating Context Usage (Memory Loading)...")
    # Simulate agents working and using tokens
    orchestrator.update_context("manager-ai", 45000)
    orchestrator.update_context("backend-api", 85000)
    orchestrator.update_context("frontend-ui", 127000) # Near limit
    orchestrator.update_context("physics-sim", 130000) # Exceeded limit

    print("\n[3] Simulating Developer Context Switching...")
    orchestrator.switch("backend-api", snapshot="pre-backend")
    orchestrator.switch("frontend-ui", snapshot="pre-frontend")
    orchestrator.switch("manager-ai")

    print("\n[4] Triggering LangGraph & TMUX Health Sweep...")
    orchestrator.health_check()

    print("\n[5] Decommissioning Agent Nodes...")
    orchestrator.stop("manager-ai")
    orchestrator.stop("backend-api")
    orchestrator.stop("frontend-ui")
    orchestrator.stop("physics-sim")

    print("\n✅ Scenario Complete.")

def parse_benchmarks():
    print("\n==================================================")
    print("📊 Benchmark Metrics Summary")
    print("==================================================")

    if not os.path.exists(LOG_FILE):
        print("No log file found to parse.")
        return

    operation_times = {}
    memory_deltas = {}

    # Regex to parse the log lines like:
    # 2026-10-01 16:04:46,857 - INFO - [BENCHMARK] stop | Time: 1.218 ms | Memory Delta: +0.00 KB
    regex = re.compile(r"\[BENCHMARK\] (\w+) \| Time: ([\d.]+) ms \| Memory Delta: ([+-][\d.]+) KB")

    with open(LOG_FILE, "r") as f:
        for line in f:
            match = regex.search(line)
            if match:
                op, t_ms, mem_kb = match.groups()
                t_ms = float(t_ms)
                mem_kb = float(mem_kb)

                if op not in operation_times:
                    operation_times[op] = []
                    memory_deltas[op] = []

                operation_times[op].append(t_ms)
                memory_deltas[op].append(mem_kb)

    print(f"{'Operation':<18} | {'Avg Time (ms)':<15} | {'Max Time (ms)':<15} | {'Peak Mem Delta (KB)':<20}")
    print("-" * 75)

    for op in operation_times.keys():
        times = operation_times[op]
        mems = memory_deltas[op]

        avg_time = sum(times) / len(times)
        max_time = max(times)
        peak_mem = max(mems, key=abs) # Get largest delta (positive or negative)

        print(f"{op:<18} | {avg_time:<15.3f} | {max_time:<15.3f} | {peak_mem:<20.2f}")

    print("==================================================\n")

if __name__ == "__main__":
    run_scenario()
    parse_benchmarks()
