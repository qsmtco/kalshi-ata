# Checkpoint Coding Prompt

## The Rule

**Never write more than 10–15 lines of code before stopping to verify.**

This is not optional. This is not a suggestion. This is the method.

Every time you write a chunk of code, you must do the following in order before touching anything else:

1. **Compile / Syntax Check** — Run `python3 -m py_compile <file>` or `node --check <file>` (for JS). Fix every error before continuing.
2. **Logic Verification** — Write a 3–5 line test snippet that proves the new code does what it's supposed to. Run it. If it fails, fix the logic before continuing.
3. **Log what you did** — Write a one-line comment in the code describing what this chunk does. If you modified a function, say so.

Then and only then: move to the next chunk.

---

## Why This Matters

Errors compound. A missing comma on line 50 becomes invisible once you've written 300 more lines. A logic bug you introduced an hour ago becomes impossible to isolate once you've buried it under more code. Checkpoint coding surfaces bugs at the moment they're created, when they're easiest to fix.

For a live trading system: every chunk you don't verify is a chunk that could lose real money.

---

## The Loop

```
WRITE (5–15 lines) → COMPILE → FIX SYNTAX ERRORS → LOGIC TEST → FIX LOGIC ERRORS → WRITE (next chunk)
```

You repeat this loop until the feature is done. You do not skip steps. You do not "write it all and test at the end."

---

## What to Do When You Start This Task

1. Read the existing code thoroughly before touching it. Understand what it does and how it fits together.
2. Identify the exact smallest change you need to make first.
3. Write that change in chunks of ≤15 lines.
4. Verify each chunk before the next.
5. After all changes compile cleanly, run a broader integration test.
6. Commit.

---

## What to Report Back

After each chunk:
- What you wrote
- Whether it compiled cleanly
- Whether the logic test passed or failed
- What you fixed if it failed

If you encounter a bug you can't immediately fix, report it immediately — do not code around it, do not skip it, do not leave it for later. Stop and report.

---

## The Task

[Briefing: describe the specific task here — e.g., "Implement Step 2.2 of the Exit Strategy Implementation Plan: add check_liquidity_exit() to exit_rules.py"]

Follow the checkpoint loop above. Report after every chunk.

---

## Hard Rules

- If `python3 -m py_compile` fails, you do not continue until it passes
- If your logic test fails, you do not continue until the test passes
- You do not write more than 15 lines between checkpoints
- You do not skip the logic test because you "feel confident" about the code
- You log each completed chunk with a brief comment

---

## Checkpoint Log Format

Every time you complete a chunk, report in this format:

```
### Checkpoint N — [description]
**Lines modified:** [file:line range]
**Compiled:** [YES / NO — error if no]
**Logic test:** [PASS / FAIL — describe what failed]
**Fixes applied:** [list if any]
```
