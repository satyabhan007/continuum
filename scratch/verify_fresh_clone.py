#!/usr/bin/env python3
"""Fresh-clone verification driver: runs the full 7-layer validation stack.

Reproduces, in one command, the exact evidence chain a fresh clone of the
repository can verify offline (no live LLM, no real tokens):

  L1 test_relay.py                    - 4x400 relay contract (simulation)
  L2 scratch/test_retry_backoff_reference.py - 18 cases / 97 assertions
  L3 scratch/tmp_adv_contract_test.py  - 5 adversarial contract tests
  L4 scratch/validate_doc_claims_run1790758990.py - round-2 claims (23)
  L5 scratch/validate_doc_claims_run1790774818.py - round-3 claims (25 with
     the run record present; 24 in fallback mode when the record is absent,
     as in any clone, since the records are deliberately untracked)
  L6 stock_analysis/scratch/validate_analysis.py - second-opinion stock
     metrics validator (self-heals a missing gitignored out/report.json by
     regenerating it with the committed analyzer, then validates)

Each layer's exit code is checked; any non-zero exit fails the driver.
Live-token tests (test_relay_live.py, test_relay_real.py) are deliberately
NOT run.

Usage: python3 scratch/verify_fresh_clone.py   (from the repo root)
Exit: 0 only if all seven layers pass.
"""
import os
import subprocess
import sys

# (label, cwd-relative-to-repo-root, command)
LAYERS = [
    ("L1 relay suite", "", [sys.executable, "test_relay.py"]),
    ("L2 retry reference (97 assertions)", "",
     [sys.executable, "scratch/test_retry_backoff_reference.py"]),
    ("L3 adversarial contract (5 tests)", "",
     [sys.executable, "scratch/tmp_adv_contract_test.py"]),
    ("L4 round-2 claims validator", "",
     [sys.executable, "scratch/validate_doc_claims_run1790758990.py"]),
    ("L5 round-3 claims validator (fallback mode in clones)", "",
     [sys.executable, "scratch/validate_doc_claims_run1790774818.py"]),
    ("L6 stock validator (self-heals report.json)", "stock_analysis",
     [sys.executable, "scratch/validate_analysis.py"]),
    ("L7 analyzer unit tests (70 tests)", "stock_analysis",
     [sys.executable, "-m", "unittest", "test_stock_analyzer"]),
]


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    failed = []
    for label, rel_cwd, cmd in LAYERS:
        cwd = os.path.join(repo_root, rel_cwd) if rel_cwd else repo_root
        print(f"--- {label} ---", flush=True)
        try:
            proc = subprocess.run(cmd, cwd=cwd, text=True,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
        except OSError as exc:
            # Never crash: a missing cwd/command is a layer failure,
            # not a driver crash (repo rule: degrade gracefully).
            print(f"    -> FAIL (could not run: {exc})")
            failed.append(label)
            continue
        # Echo the tail of each layer's output so a run log shows the
        # PASS/ALL-PASSED markers, not just the exit code.
        tail = proc.stdout.strip().splitlines()[-3:]
        for line in tail:
            print(f"    {line}")
        status = "PASS" if proc.returncode == 0 else "FAIL"
        print(f"    -> {status} (exit={proc.returncode})")
        if proc.returncode != 0:
            failed.append(label)

    print()
    if failed:
        print(f"FRESH-CLONE VERIFICATION FAILED: {len(failed)} layer(s): "
              + ", ".join(failed))
        sys.exit(1)
    print("FRESH-CLONE VERIFICATION PASSED: all 7 layers green "
          "(exit=0 each).")
    sys.exit(0)


if __name__ == "__main__":
    main()
