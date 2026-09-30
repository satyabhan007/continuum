# Continuum

This repository contains scripts and tools for the Continuum project.

## Overview

The Continuum project aims to provide seamless transitions and orchestrations between Hermes sessions (harnesses) with context preservation. It includes tools for switching active harnesses while maintaining filesystem snapshots, context, and optionally separate Git worktrees to avoid conflicts. It also offers multi-harness orchestration to coordinate states across several running sessions.

## Getting Started

### Prerequisites
- Bash
- Python 3
- `tmux` (for background command execution in the harness switch script)

### Scripts
- `hermes_switch_harness.sh`: A utility to transition a Hermes session from one local harness to another.
- `multi_harness_orchestrator.py`: An orchestrator for managing and transitioning state across multiple Hermes harnesses (start, switch, stop, list).
- `test_relay.py`: A test script to verify state relays across harnesses using the orchestrator.

### Usage
For the shell script:
```bash
./hermes_switch_harness.sh --title my-session --worktree
```

For the Python orchestrator:
```python
from multi_harness_orchestrator import Orchestrator

# Example usage goes here
```
