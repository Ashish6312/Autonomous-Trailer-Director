# AI collaboration

## Tools

- **Claude Code** (Anthropic, Claude Opus 5.5) in the final phase: reading
  the repository, designing changes, writing code, tests and documentation,
  and running the test suite, Ruff and the CLI.
- **Claude (claude-opus-5-5)** is also a component of the product: the
  optional LLM planner and repairer (`--mode llm`, `--repair-mode llm`).
- **Ruff** (via `uvx`) and **pytest** checked every change.
- For the Gemini adapter, the installed `google-genai` SDK (2.25.0) was
  inspected first: how `Client()` fails without a key, the `HttpOptions`
  timeout and retry fields, and the usage field names. The adapter was
  written against that, not from memory. Its tests use a fake client. The
  developer then ran one live vision call on the test image with their own
  key. It showed an SDK warning about automatic function calling; since the
  gateway passes no tools, that feature is now disabled explicitly.

This document covers the final phase (evaluation, hardening, release),
for which a record exists. No record of the earlier sessions was
available, so they are not described here.

## How AI output was treated

The same rule applies to the product and to how it was built: generated
output is a proposal, and something deterministic decides whether it stands.

- Every code change was followed by the full test suite, `ruff check` and
  `ruff format --check`; nothing was reported as done while any of them
  failed.
- Claims about behaviour were checked against run records or files, not
  against what was expected to happen.
- Live model calls were made by the developer in their own shell with their
  own key. The key was never shown to the assistant, and project code never
  reads it (the Anthropic SDK resolves credentials).

## Why verification is separate from generation

A model is useful for choosing moments, ordering them and writing a hook. It
is unreliable at rights windows, spoiler levels, timecodes and contract
clauses, and it can be steered by text inside the evidence. So:

- the model only sees evidence the constraint engine has already approved,
  with instruction-like text removed;
- its output is untrusted: the normaliser checks every ID, the claims check
  requires the hook to quote real usable dialogue, the decision guard checks
  that a repair changed only what it was allowed to, and the constraint
  engine decides eligibility;
- a plan the planner calls "safe" is re-checked like any other
  (`test_engine_rejects_a_plan_the_planner_calls_approved`).

## Recorded examples

These happened during the final phase. Each shows either an AI suggestion
being rejected or corrected, or the AI catching a problem.

1. **Wrong replay source (instruction corrected).** The requested command for
   verifying the first live run was `plan --audience family --mode replay`.
   The assistant checked the CLI and found the default replay directory is
   `examples/planner_runs`, the hand-authored fixture. That command would have
   "verified" the fixture, not the live recording. It used
   `--replay-dir runs/planner_runs` instead, and the README now says so.

2. **A test that passed for the wrong reason (AI mistake, caught in review).**
   The assistant's first test for the subtitle reading-speed rule cut SC09
   at 08:59-09:10 around DLG_036, which is spoken at 09:12-09:15. The test
   passed because the rule reads declared lines. The assistant noticed on
   review and corrected the clip to contain the line
   (`tests/test_hardening.py`).

3. **Scenario design error exposed by the evaluation (AI mistake).** The first
   version of the continuity scenario (S13) used SC01 for every audience.
   Its expectation failed for family and young adult: SC01 is barred for them
   by performer rights, so repair replaced it and the continuity warning
   disappeared. The expectation was right and the scenario was wrong. The
   scenario now uses a prop-sharing pair each audience can actually use, and
   family is reported as "not applicable" with the reason. No two
   family-usable scenes share a visible prop, which is itself a finding.

4. **Rejected: editing data to silence a warning.** The new reciprocity check
   found that CHAR_SHANKAR does not list CHAR_BANSI. Adding the missing record
   would make `validate-data` report zero warnings, but it would change the
   evidence fingerprint that the live recordings were made against. The change
   was rejected; the warning is documented and pinned by a test.

5. **Rule too strict on first design (AI mistake, caught by existing data).**
   The hook-truth check was first designed to reject any hook not quoted from
   a line in the cut. Run against every existing plan before merging, it
   flagged the hand-authored dialect-region fixture, whose hook quotes a real,
   usable line (DLG_003) that is not in its clips. That is not a fabricated
   claim, so the rule was refined: an invented or ineligible quote is an
   error, and a real line outside the cut is a warning.

6. **Overstated status (claim corrected).** After the young-adult live run,
   a summary described the live LLM pipeline as "validated". The run record
   shows 0 repair attempts and no `runs/repair_runs/` recording, because
   Claude's plan was eligible first time. So the live repair path has still
   never executed, and the documentation says exactly that.

7. **Tests protecting intent, not implementation.** Introducing the hook check
   broke `test_engine_rejects_a_plan_the_planner_calls_approved`, whose stub
   plan used the made-up hook "Stub hook". The test's purpose is the engine
   rejecting a spoiler, so the stub was given a real line. The check itself
   was not weakened. The review-sheet test was also fixed: its first version
   searched for the word "score" and failed on the sheet's own instruction
   "do not score". It now asserts the table structure.

8. **Latent bug found by an independent re-check.** In the final audit, the
   exported trailers were re-evaluated from their JSON by a separate script.
   The dialect-region trailer came out `REJECTED` instead of
   `PASS_WITH_WARNINGS`. The cause: `campaign_context` compared the audience
   with `is AudienceType.DIALECT_REGION`, so a plain `"dialect_region"`
   string got the country territory (`IN`), and the region-only performer
   licence then failed. Every caller in the code passed the enum, so no
   output was wrong, but the function now coerces its argument and a
   regression test covers the string case.

## What was not delegated

- Deciding what counts as acceptable is data and code, not a model output:
  rating policies, rights windows and the repair strategy table.
- Creative quality and cultural accuracy go to people
  (`evaluation/human_review.md`, the human approvals in each trailer).
- Spending money: the developer ran every live call themselves.
