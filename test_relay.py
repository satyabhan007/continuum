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

    def test_start_harness(self):
        result = self.orchestrator.start("harness-1")
        self.assertTrue(result)

        state = self.orchestrator.get_state()
        self.assertIn("harness-1", state["harnesses"])
        self.assertEqual(state["harnesses"]["harness-1"]["state"], "running")

    def test_switch_harness(self):
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

    def test_start_existing_harness(self):
        self.orchestrator.start("harness-1")
        result = self.orchestrator.start("harness-1")
        self.assertFalse(result)

    def test_switch_nonexistent_harness(self):
        result = self.orchestrator.switch("harness-1")
        self.assertFalse(result)

    def test_stop_nonexistent_harness(self):
        result = self.orchestrator.stop("harness-1")
        self.assertFalse(result)

if __name__ == "__main__":
    unittest.main()
