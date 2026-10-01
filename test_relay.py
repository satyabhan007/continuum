#!/usr/bin/env python3
import unittest
import os
import json
from multi_harness_orchestrator import MultiHarnessOrchestrator, STATE_FILE

TEST_STATE_FILE = ".test_harness_state.json"

class TestRelay(unittest.TestCase):
    def setUp(self):
        # Clear out test state file before each test
        if os.path.exists(TEST_STATE_FILE):
            os.remove(TEST_STATE_FILE)
        self.orchestrator = MultiHarnessOrchestrator(state_file=TEST_STATE_FILE)

    def tearDown(self):
        # Clean up test state file after each test
        if os.path.exists(TEST_STATE_FILE):
            os.remove(TEST_STATE_FILE)

    def test_start_harness_basic(self):
        result = self.orchestrator.start("harness-1")
        self.assertTrue(result)

        state = self.orchestrator.get_state()
        self.assertIn("harness-1", state["harnesses"])
        self.assertEqual(state["harnesses"]["harness-1"]["state"], "running")
        self.assertFalse(state["harnesses"]["harness-1"]["worktree"])
        # With default agent_type="hermes", the command is no longer None
        self.assertEqual(state["harnesses"]["harness-1"]["background_cmd"], "hermes --continue")

    def test_start_harness_with_parameters(self):
        result = self.orchestrator.start("harness-full", worktree=True, background_cmd="echo hello", agent_type="jcode")
        self.assertTrue(result)

        state = self.orchestrator.get_state()
        self.assertIn("harness-full", state["harnesses"])
        self.assertTrue(state["harnesses"]["harness-full"]["worktree"])
        self.assertEqual(state["harnesses"]["harness-full"]["background_cmd"], "echo hello")
        self.assertEqual(state["harnesses"]["harness-full"]["agent_type"], "jcode")

    def test_start_harness_default_agent_commands(self):
        self.orchestrator.start("harness-opencode", agent_type="opencode")
        self.orchestrator.start("harness-antigravity", agent_type="antigravity")

        state = self.orchestrator.get_state()
        self.assertEqual(state["harnesses"]["harness-opencode"]["background_cmd"], "opencode run")
        self.assertEqual(state["harnesses"]["harness-opencode"]["session_name"], "opencode_harness-opencode")

        self.assertEqual(state["harnesses"]["harness-antigravity"]["background_cmd"], "antigravity daemon")
        self.assertEqual(state["harnesses"]["harness-antigravity"]["session_name"], "antigravity_harness-antigravity")

    def test_start_existing_harness(self):
        self.orchestrator.start("harness-1")
        result = self.orchestrator.start("harness-1")
        self.assertFalse(result)

    def test_switch_harness_basic(self):
        self.orchestrator.start("harness-1")
        self.orchestrator.start("harness-2")

        # Switch to harness-1
        result = self.orchestrator.switch("harness-1")
        self.assertTrue(result)
        state = self.orchestrator.get_state()
        self.assertEqual(state["active_harness"], "harness-1")

        # Switch to harness-2 with a snapshot
        result = self.orchestrator.switch("harness-2", snapshot="pre-switch")
        self.assertTrue(result)
        state = self.orchestrator.get_state()
        self.assertEqual(state["active_harness"], "harness-2")

    def test_stop_harness(self):
        self.orchestrator.start("harness-1")
        self.orchestrator.switch("harness-1")

        result = self.orchestrator.stop("harness-1")
        self.assertTrue(result)

        state = self.orchestrator.get_state()
        self.assertNotIn("harness-1", state["harnesses"])
        self.assertIsNone(state["active_harness"])

    def test_switch_nonexistent_harness(self):
        result = self.orchestrator.switch("harness-1")
        self.assertFalse(result)

    def test_stop_nonexistent_harness(self):
        result = self.orchestrator.stop("harness-1")
        self.assertFalse(result)

    def test_corrupted_state_file(self):
        # Create a corrupted state file
        with open(TEST_STATE_FILE, "w") as f:
            f.write("{ invalid json")

        # Load orchestrator with corrupted state; it should handle gracefully
        orchestrator = MultiHarnessOrchestrator(state_file=TEST_STATE_FILE)

        # State should be empty and not crash
        self.assertEqual(orchestrator.get_state()["harnesses"], {})
        self.assertIsNone(orchestrator.get_state()["active_harness"])

        # Try to start a harness to ensure saving overwrites bad state
        orchestrator.start("harness-recovery")

        with open(TEST_STATE_FILE, "r") as f:
            state = json.load(f)
            self.assertIn("harness-recovery", state["harnesses"])

    def test_list_harnesses(self):
        self.orchestrator.start("harness-a")
        self.orchestrator.start("harness-b")
        self.orchestrator.switch("harness-a")

        # Call list_harnesses and ensure it returns the correct structure
        harnesses = self.orchestrator.list_harnesses()

        self.assertIn("harness-a", harnesses)
        self.assertIn("harness-b", harnesses)

    def test_health_check_stale(self):
        # Start a harness with a background command (which expects a tmux session)
        self.orchestrator.start("harness-dead", background_cmd="echo hello")
        # Ensure it's marked as running
        self.assertEqual(self.orchestrator.get_state()["harnesses"]["harness-dead"]["state"], "running")

        # Run health check (will fail to find tmux session)
        stale = self.orchestrator.health_check()

        self.assertIn("harness-dead", stale)
        self.assertEqual(self.orchestrator.get_state()["harnesses"]["harness-dead"]["state"], "stale")

    def test_update_context_limits(self):
        self.orchestrator.start("harness-token")

        # Update tokens (below limit)
        self.orchestrator.update_context("harness-token", 50000)
        state = self.orchestrator.get_state()
        self.assertEqual(state["harnesses"]["harness-token"]["tokens_used"], 50000)
        self.assertEqual(state["harnesses"]["harness-token"]["state"], "running")

        # Update tokens (exceed limit)
        self.orchestrator.update_context("harness-token", 130000)
        state = self.orchestrator.get_state()
        self.assertEqual(state["harnesses"]["harness-token"]["tokens_used"], 130000)
        self.assertEqual(state["harnesses"]["harness-token"]["state"], "context_exceeded")

if __name__ == "__main__":
    unittest.main()
