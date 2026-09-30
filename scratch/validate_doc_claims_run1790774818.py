#!/usr/bin/env python3
"""scratch/validate_doc_claims_run1790774818.py - doc-vs-requirements checker.

Programmatically validates that scratch/relay_test_run1790774818.md satisfies
the task contract, instead of trusting eyeball inspection:

  1. Exact path + exactly 3 sections with the required titles, in order.
  2. Section 1 carries the API sketch elements (RetryPolicy, NonRetryableError,
     retry_with_backoff, call-site wrap).
  3. Section 2 carries every rule R1..R7 and its R3 table arithmetic matches
     the R1 formula recomputed from scratch (no copy-paste trust).
  4. Section 3 carries every failure mode FM1..FM12 plus exactly 3 escape
     hatches.
  5. Every fenced python block in the doc is syntactically valid Python
     (wrapped in an async function where top-level `await` requires it).
  6. The doc's stated FM4 amplification math (3*5*4) and FM6 token example
     recompute correctly.
  7. No commit contains the deliverable (untracked only).

Run: python3 scratch/validate_doc_claims_run1790774818.py
"""

import ast
import os
import re
import subprocess
import sys

DOC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "relay_test_run1790774818.md")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}  [{detail}]")


def main():
    text = open(DOC, encoding="utf-8").read()
    lines = text.splitlines()

    # ---- 1. Path + exactly 3 sections, required titles, in order
    check("1a file exists at required path", os.path.isfile(DOC), DOC)
    headers = [(i + 1, l) for i, l in enumerate(lines)
               if l.startswith("## Section")]
    check("1b exactly 3 section headers", len(headers) == 3,
          f"found {len(headers)}: {[h[1] for h in headers]}")
    titles = [h for _, h in headers]
    check("1c section titles match requirement",
          titles == ["## Section 1 - API sketch",
                     "## Section 2 - Backoff timing rules",
                     "## Section 3 - Failure modes"], f"titles={titles}")
    order = [n for n, _ in headers]
    check("1d sections in required order", order == sorted(order),
          f"line numbers={order}")

    def body(start_hdr_idx):
        """Text of the section starting at headers[start_hdr_idx]."""
        start = headers[start_hdr_idx][0]
        end = headers[start_hdr_idx + 1][0] - 1 if start_hdr_idx + 1 < 3 \
            else len(lines)
        return "\n".join(lines[start:end])

    s1, s2, s3 = body(0), body(1), body(2)

    # ---- 2. Section 1 API sketch elements
    for token, why in [
            ("class RetryPolicy", "policy dataclass"),
            ("from_config", "config loader"),
            ("class NonRetryableError", "fail-fast exception"),
            ("async def retry_with_backoff", "the helper"),
            ("on_attempt", "per-attempt observer (FM6)"),
            ("adapter.run(prompt, workdir=os.getcwd())", "call-site wrap"),
    ]:
        check(f"2 S1 has {why}", token in s1, token)

    # ---- 3. Section 2 rules R1..R7 present
    missing = [f"R{i}" for i in range(1, 8) if f"R{i} " not in s2
               and f"R{i} -" not in s2]
    check("3a all rules R1-R7 labeled", not missing, f"missing={missing}")

    # R3 table arithmetic recomputed from the R1 formula in the doc:
    #   w_k = min(max_delay, base_delay * backoff_factor ** (k-1))
    base, factor, max_delay, attempts = 1.0, 1.5, 30.0, 3
    waits = [min(max_delay, base * factor ** (k - 1))
             for k in range(1, attempts)]           # waits before retries
    check("3b R3 waits recompute to 1.0/1.5",
          [round(w, 6) for w in waits] == [1.0, 1.5], f"waits={waits}")
    check("3c R3 worst case 2.50s stated and true",
          "2.50s" in s2 and abs(sum(waits) - 2.50) < 1e-9,
          f"sum={sum(waits):.2f}")
    jitter_min = sum(w / 2 for w in waits)           # equal jitter floor
    check("3d R3 jitter minimum 1.25s stated and true",
          "1.25s" in s2 and abs(jitter_min - 1.25) < 1e-9,
          f"jitter_min={jitter_min:.2f}")
    check("3e R1 cap applies unconditionally (deadline=None case stated)",
          "UNCONDITIONALLY" in s2 and "deadline" in s2)
    check("3f R2 equal jitter [w/2, w] stated",
          "[w_k/2, w_k]" in s2 or "w_k/2, w_k" in s2)

    # ---- 4. Section 3 failure modes FM1..FM12 + 3 hatches
    fms = re.findall(r"^\|\s*(FM\d+)\s*\|", s3, re.M)
    expected_fms = [f"FM{i}" for i in range(1, 13)]
    check("4a all FM1-FM12 rows present, in order",
          fms == expected_fms, f"found={fms}")
    check("4b exactly 3 escape hatches stated", "escape hatches" in s3
          and len(re.findall(r"^\d\.\s", s3, re.M)) == 3,
          f"hatch lines={re.findall(r'^\d\. .*', s3, re.M)}")

    # ---- 5. Fenced python blocks parse
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    check("5a doc has python blocks", len(blocks) >= 2, f"blocks={len(blocks)}")
    for bi, b in enumerate(blocks):
        try:
            ast.parse(b)
            check(f"5b block {bi + 1} parses as module", True)
        except SyntaxError:
            try:  # call-site fragment: top-level await needs an async wrapper
                wrapped = "async def __w():\n" + \
                    "".join(f"    {l}\n" for l in b.splitlines())
                ast.parse(wrapped)
                check(f"5b block {bi + 1} parses (async wrapper)", True)
            except SyntaxError as e:
                check(f"5b block {bi + 1} parses", False, f"{e}")

    # ---- 6. Stated arithmetic in FM4 / FM6 recomputes
    check("6a FM4 amplification 3*5*4=60 stated and true",
          "60 runs" in s3 and 3 * 5 * 4 == 60)
    check("6b FM6 books-last-only example consistent",
          "tokens_used or 500" in s3 and "200" in s3)

    # ---- 7. No commit contains the deliverable
    r = subprocess.run(["git", "log", "--all", "--oneline", "--",
                        "scratch/relay_test_run1790774818.md"],
                       cwd=ROOT, capture_output=True, text=True)
    check("7a no commit ever touched the deliverable",
          r.returncode == 0 and r.stdout.strip() == "",
          f"git log output={r.stdout.strip()!r}")
    r2 = subprocess.run(["git", "status", "--porcelain", "--",
                         "scratch/relay_test_run1790774818.md"],
                        cwd=ROOT, capture_output=True, text=True)
    check("7b deliverable untracked (?? ), not committed",
          r2.stdout.strip() == "?? scratch/relay_test_run1790774818.md",
          f"status={r2.stdout.strip()!r}")

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
