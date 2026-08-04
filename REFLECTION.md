# REFLECTION.md

A framework for structured retrospectives on CodeHarness development.
Fill this in at the end of each development session (or each task batch)
to capture what the next session needs to know.  Answer every section
honestly and concretely — one-line platitudes are worse than nothing.

## 1. What went well

- (What worked as planned? Which decisions held up under use?)

## 2. What did not go well

- (What broke, slowed us down, or needed rework? Include test failures,
  lint churn, and spec mismatches.)

## 3. Deviations from the plan

- (Where did the implementation differ from the plan/SPEC, and why?
  Note any deliberate deviations that must not be "fixed" later.)

## 4. Design decisions that deserve revisiting

- (Mechanisms with known rough edges: classifier regex coverage, the
  loop's conversation shape for the real API, empty tool schemas, ...)

## 5. What would we do differently next time

- (Process and architecture lessons, not just code.)

## 6. Open questions / follow-ups

- (Anything deferred: per-tool JSON schemas, provider support beyond
  DeepSeek, sandboxing for run_shell, LICENSE file before publishing.)
