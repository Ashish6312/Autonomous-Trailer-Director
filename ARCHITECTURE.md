# Architecture

This document describes the system as it exists today.

**The planner proposes; the constraint engine decides eligibility.**

**The model proposes; deterministic verification decides.** An
optional Claude-backed planner and repairer sit behind the same interfaces
as the mock and replay planners. Their output is untrusted exactly like
theirs: normalised, checked against the repair decisions, and judged by the
constraint engine. Only the `llm` mode opens a network connection; mock and
replay modes, and the whole test suite, run offline.

## End-to-end flow

```
Input                 data/**/*.json -> validation -> EpisodePackage (+ evidence fingerprint)
  |
Evidence / story      story map (characters, relationships, events, beats, conflicts, stakes,
understanding         protected reveals, hooks) + spoiler map; every item cites its source IDs
  |
Constraint map        rule catalogue + policies + rights + budget; the constraint engine judges
                      every scene, line and music asset for the audience/date/territory -> evidence pool
  |
Audience strategy     profile + rating policy -> positioning, themes, tones, avoid list, length;
                      stereotyping preferences withheld
  |
Creative planner      mock | replay | Claude: proposes clips, order, lines, music, hook, rationale
  |                   (UNTRUSTED)
Independent verifier  normaliser (shape, IDs) -> hook truth -> constraint engine -> verdict
  |
Repair / replan       violation -> allowed actions (deterministic) -> repairer -> decision guard
  |                   -> re-verify; replan on changed date, territory, contract or subtitle
Final EDL             artifacts.py: segments with timecodes, video/audio/subtitles, evidence,
  |                   risk flags, approvals, cost, lower-cost fallback
Audit / evaluation    RepairRun record + audit trail; evaluation scenarios; human review sheet
```

**Planning.** A planner receives the story map, the audience strategy and
the evidence pool, and returns a JSON payload: title, audience promise
(`positioning`), hook, rationale and clips (scene, timecodes, lines, music,
purpose, reason, evidence IDs). The mock planner scores eligible material
with explicit heuristics; Claude does the same job with the filtered context.
Neither decides eligibility.

**Memory and evidence.** There is no conversational memory. The only state is
the validated `EpisodePackage`, identified by its content fingerprint. Every
derived artefact (story map, pool, run record, recording) names that
fingerprint, so a replay recorded against other evidence is flagged. Within a
run, the `RepairRequest` carries the current plan, decisions and excluded IDs
to the repairer. Across runs, recordings under `runs/` are the memory, and
they are replayed rather than re-generated.

**Multimodal processing.** None is performed. The package describes footage
(timecodes, cast, locations, visual tags, props, subtitle text); no video or
audio is decoded. Segment `video` and `audio` blocks restate that evidence.
Media checks are listed in KNOWN_LIMITATIONS.md.

**Human control.** Verdicts decide what may be proposed for release, not what
is released. Every trailer lists the approvals it needs: editorial (always,
plus continuity and reading-speed findings), legal (always, plus approval
clauses), cultural (dialect region, dialect/subtitle findings, withheld
stereotypes), and marketing (always: title, promise, hook). Review warnings
are never auto-repaired; they are routed to these roles.

**Transitions, voice-over, text cards.** `edit_fields.derive_edit_fields`
adds these to each exported plan after the repair loop accepts it
(`artifacts._edl`): a cut or dissolve per consecutive clip pair, an empty
voice-over list, and the verified hook as one text card.
`edit_fields.check_edit_fields` then verifies them: transitions must join
consecutive existing clips with an allowed type; voice-over and card text
must cite existing lines and quote dialogue the audience may use
(`planning.claims.quote_status`), and a hook card must equal the verified
hook. The result is recorded under `validation.edit_fields`. The planner
schema and the recordings are untouched.

## Trust boundaries

```
 TRUSTED (validated evidence, deterministic code)          UNTRUSTED
 ------------------------------------------------          ---------
 data/**/*.json --validator--> EpisodePackage
 constraint engine, story map, strategy, pool
 repair strategy table (what may change)
            |
            |  llm.context: filtered + screened context  -->  Claude (or any planner / repairer)
            |                                                    |
            |  <-- payload (JSON-like, any content) -------------+
            v
 normaliser (shape, IDs exist) -> hook truth (quotes real, usable dialogue)
 -> decision guard (only allowed components changed) -> constraint engine (eligibility)
 -> verdict: PASS | PASS_WITH_WARNINGS | REJECTED
```

1. **Evidence -> code.** Evidence is validated before anything uses it;
   free-text fields are never interpreted as instructions by any code path.
2. **Code -> model.** Only `llm.context` builds what the model sees:
   engine-approved evidence, no protected-reveal content, no production or
   continuity notes, no synopsis, and no text that reads like an instruction
   (`llm.screening` withholds it, and a final scan refuses to send a context
   that still contains any). Credentials are resolved inside the SDK and
   never pass through project code.
3. **Model -> code.** Every planner and repairer payload - LLM, replay or
   mock - is untrusted. The normaliser, the decision guard and the
   constraint engine sit between it and any accepted plan; there is no path
   around them.

## Layers

```
Evidence              data/**/*.json
   |
   v
Domain models         trailer_director.domain       typed, immutable records; TrailerClip / TrailerCandidate
   |
   v
Dataset validation    trailer_director.data         -> read-only EpisodePackage, or a report of every error
   |
   v
Constraint engine     trailer_director.constraints  rules -> EligibilityResult (used twice: pool and verdict)
   |
   v
Story map             trailer_director.story        deterministic baseline, every item source-referenced
   |
   v
Audience strategy     planning.strategy             derived from the loaded audience profile + rating policy
   |
   v
Evidence pool         planning.pool                 each scene, line and music asset run through the engine
   |
   v
Planner interface     planning.planner              plan(story_map, strategy, pool) -> PlannerResponse
   |
   v
Mock / replay / LLM    planning.mock, planning.replay, llm   untrusted JSON-like payload
   |
   v
Normaliser            planning.normalizer           trust boundary: shape + every ID exists -> PlanProposal
   |
   v
TrailerCandidate      (reused)
   |
   v
Constraint engine     evaluate_trailer(candidate, context) -> EligibilityResult
   |
   v
PlanningRun           planning.pipeline             traceable record of a single plan

Repair loop      trailer_director.repair
   initial plan  --(planner error)-->  retry with backoff  -->  replay fallback
        |
        v
   constraint engine -> diagnose failing clips
        |  not eligible
        v
   repair decisions (violation -> strategy table)
        v
   repair planner: deterministic | replay | LLM (carries out decisions)
        -> normaliser -> decision guard -> constraint engine
        |  repeat until eligible, attempts or budget run out, or no progress
        v
   RepairRun: every attempt, decisions, clip changes, audit trail, budget usage, final status
```

The CLI (`trailer_director.cli`) is a thin shell over these layers.

## Components

**Evidence, domain models, dataset validation.** One JSON file per
real-world owner. Frozen Pydantic models with enforced ID formats and
millisecond timecodes. Validation proves the evidence is internally
consistent and produces an `EpisodePackage` whose lookups raise
`UnknownEntityError` for any invented ID. `EpisodePackage.fingerprint()` is a
content hash that later artefacts record so they can name the evidence they
were built from.

**Constraint engine.** Small independent rules, each with a `rule_id`
and `evaluate(clip, context, evidence) -> list[ConstraintViolation]`,
grouped by category (source, timecode, spoiler, rating, misleading
context, rights, contract clauses, performance info, trailer length, and
the review rules for subtitle safety, dialect terms and prop continuity). The
engine runs them all and never short-circuits; `eligible` is true only when
there is no `error`.

**Story map.** `build_story_map` reorganises structured evidence into
premise, characters with arcs, relationships, events, emotional beats,
conflicts, stakes, protected reveals, trailer hooks and scene functions.
It is a deterministic baseline, not story understanding: it exists so the
planner contract is fixed before a model-generated story map is introduced.
`unresolved_references` walks any story map and reports IDs that do not
exist, so a future model-generated map can be checked the same way.

**Audience strategy.** Built from the loaded audience profile and
rating policy, not copied from them. The one planning-specific addition is
`HOOK_TYPES`, an explicit table of which scene functions make the best
openings for each audience.

**Evidence pool.** Every scene (whole), every line (cut tightly on its
own timecodes) and every music asset is evaluated by the constraint engine
for the campaign context. Eligible IDs are kept; eligible items with
warnings are listed as `flagged`; ineligible items are kept in `rejected`
with their violations, so a planner can see why something is unavailable.

**Planners.** `Planner` is a Protocol with `mode`, `version` and
`plan(story_map, strategy, pool) -> PlannerResponse`. A response carries a
JSON-like `payload`, which is untrusted whatever produced it.

- `MockPlanner` ranks scenes and lines with explicit heuristics
  (`planning/heuristics.py`): hook type for the audience, tone match,
  importance, trailer-hook lines, regional address terms for the
  dialect-region audience, and historical engagement as a small supporting
  signal. Every point awarded carries a reason and evidence IDs, which become
  the clip's rationale. It draws only from pool-eligible, unprotected lines.
- `ReplayPlanner` returns a recorded response from
  `examples/planner_runs/<audience>.json` (versioned format, with the
  episode and evidence fingerprint it was recorded against). A fingerprint
  mismatch is reported as a warning.

**Normaliser.** Converts the payload into a `PlanProposal` (reusing
`TrailerCandidate`) or returns structured `PlanningIssue`s: malformed
structures, missing fields, audience mismatch, invalid clips, unknown
scene/dialogue/music/evidence IDs, missing rationale. It never raises on bad
input and reports every problem in one pass. It checks that IDs exist, not
whether they are allowed.

**Pipeline.** `run_planning` runs story map -> strategy -> pool ->
planner -> normaliser -> engine and returns a `PlanningRun`: content-derived
`run_id`, episode, evidence version and fingerprint, context, mode, planner
version, status (`eligible`, `rejected`, `invalid_output`,
`planner_failed`), proposal, eligibility result, issues, duration, model
calls and estimated model cost from the cost sheet. There is no repair or
retry.

**Repair loop.** `RepairLoop.run(planner, context)` and
`RepairLoop.replan(previous_plan, new_context)`:

- *Retry and fallback.* Planner errors are retried with exponential backoff
  (`max_provider_retries`, `retry_backoff_seconds` from the cost sheet). If
  the provider is still unavailable and the cost sheet says
  `use_replay_fixtures`, the fallback (replay) planner is used; otherwise the
  run ends as `planner_unavailable`. Repairs degrade the same way: if the
  repair planner (e.g. the LLM) is unavailable, the fallback repairer
  (deterministic) takes over for the rest of the run, and its output is
  verified exactly like any other repair.
- *Decision layer.* `repair/strategy.py` maps each blocking violation
  (reason code + entity type) to a `RepairStrategy`: a scope (music,
  dialogue, clip, trailer) and allowed actions, least invasive first.
  `decide_repairs` produces one `RepairDecision` per affected part of a clip,
  with `reason_code`, the triggering violations and anything a clip-level
  decision also resolves. This is deterministic and is where a future model
  plugs in: a model may choose *how* to carry out a decision, not *what* is
  allowed to change.
- *Verification.* `diagnose` turns the engine result into per-clip
  diagnoses. `clip_changes` compares plans component by component (scene,
  timecodes, dialogue, music, purpose) and classifies each clip (kept, music
  swapped/removed, re-cut, replaced, dropped, added), never trusting the
  repair planner's account. `check_repair` rejects a repair that changes an
  undecided clip, changes a component no decision allows, changes a purpose,
  adds a clip, leaves a failing clip unchanged, or returns the same plan.
  `check_rationales` warns when a clip's rationale does not cite its scene
  and lines.
- *Repair planners.* `RepairPlanner.repair(request)` receives the current
  plan, the diagnoses, the decisions, the pool and the excluded IDs.
  `DeterministicRepairPlanner` carries out decisions with the planning heuristics
  (swap/remove music, re-cut in scene, replace preferring the same dramatic
  function, drop). `ReplayRepairPlanner` returns recorded repair responses.
  Repair output goes through the same normaliser as a plan.
- *Selective replanning.* Only what the decisions allow may change; passing
  clips are returned byte-for-byte or the attempt is discarded. `replan` re-verifies an
  existing plan under new conditions (date, territory) and repairs only what
  broke - for example, swapping expired music while keeping every shot.
- *Budget.* A ledger charges each model call at the cost-sheet rate
  (`planner_model_call`, `repair_model_call`) and refuses a call that would
  exceed `max_model_calls` or `max_estimated_total_cost`. Deterministic
  planners make no model calls. Repair attempts are capped separately.
- *Audit trail.* Built from the recorded attempts: initial candidate,
  verification, violations, repair decisions, changed components,
  re-verification, final decision (plus planner failures and rejected
  repairs).
- *Outcome.* `accepted`, `attempts_exhausted`, `budget_exhausted`,
  `planner_unavailable` or `repair_failed`. Only an eligible, verified plan is
  returned as `final_proposal`; otherwise the last engine result is reported
  and nothing is accepted.

**LLM planner and repairer.** `llm.LLMPlanner` implements `Planner`
and `llm.LLMRepairPlanner` implements `RepairPlanner`; nothing else in the
pipeline knows a model is involved.

```
story map + audience strategy + eligible evidence pool   (+ repair decisions)
        |  llm.context: filtered, spoiler-safe, no free-text notes
        v
Claude (claude-opus-5-5, adaptive thinking, structured JSON output)
        |  UNTRUSTED PLAN / REPAIR
        v
normaliser -> decision guard -> constraint engine -> PASS / VIOLATIONS -> repair decisions -> ...
```

- *What the model sees* (`llm/context.py`): the spoiler-safe premise, the
  audience strategy, and only the evidence the engine already marked
  eligible - scenes that have usable lines, those lines (minus protected
  reveals), eligible music - plus the IDs of unavailable items with their
  blocking reason codes. It never receives the raw dataset, the full
  synopsis, protected-reveal content, or free-text production and
  continuity notes. For a repair it also gets the current plan and the
  repair decisions (allowed actions, preferred action, what to preserve) and
  the excluded IDs.
- *Instructions* (`llm/prompts.py`): static system prompts that state the
  evidence is data, never instructions; forbid invented IDs; and, for
  repairs, require copying undecided clips verbatim and staying within each
  decision.
- *Output* (`llm/schema.py`): structured outputs constrain the response to
  the same JSON payload the normaliser accepts from every planner. That
  guarantees shape only; IDs, timecodes and eligibility are still verified
  downstream. Unparseable text is passed on as-is and reported by the
  normaliser as malformed output.
- *Failures* map to `PlannerError`, which the repair loop already handles.
  `retryable` distinguishes 429 / 5xx / connection errors (retried with the
  cost-sheet backoff) from bad requests, authentication, missing
  credentials, refusals and truncated output (not retried; straight to the
  replay fallback). SDK-level retries are disabled so the cost sheet is the
  single retry policy. Server-side refusal fallback (`fallbacks: "default"`)
  is enabled on every request.
- *Cost*: each call is charged at the cost-sheet rate as before; the actual
  input and output token counts are recorded on each attempt and in the run
  budget.
- *Recording* (`llm/recording.py`): `--record-dir` saves live responses in
  the existing replay formats, so any live run can be replayed offline and
  reproduced exactly.

## Interfaces

```python
# planning.planner
class Planner(Protocol):
    mode: PlannerMode  # mock | replay | llm
    version: str

    def plan(self, story_map, strategy, pool) -> PlannerResponse: ...


# repair.planners
class RepairPlanner(Protocol):
    mode: PlannerMode
    version: str

    def repair(self, request: RepairRequest) -> PlannerResponse: ...
```

`PlannerResponse` carries the untrusted `payload`, `model_calls`, optional
`usage` (input/output tokens and the model that served the call),
`latency_seconds` (live calls only) and `issues` (e.g. evidence withheld, a
replay recorded against other evidence). `RepairRequest` carries the
current plan, per-clip diagnoses, the repair decisions (allowed and
preferred actions, components to preserve), the pool and excluded IDs.

## Verification loop

```
initial plan --(PlannerError)--> retry (backoff) --(still failing)--> fallback planner
     |
normalise + hook truth --(invalid)-------------------+
     |                                               |
engine: eligible? --yes--> ACCEPTED                  |
     | no                                            v
diagnose -> decide_repairs (strategy table) -> repair request (proposal or none)
     |
repairer --(PlannerError)--> retry --> fallback repairer (sticky for the run)
     |
normalise -> clip_changes -> check_repair (decision guard) -> engine
     |
loop until: eligible | attempts used | budget refused | no progress
```

## Failure handling and fallback

| Failure | Classified as | Loop behaviour |
|---|---|---|
| 429, 5xx, 408, 409, connection error, timeout | retryable `PlannerError` | retried `max_provider_retries` times with exponential backoff, then fallback |
| 400/401/403, missing credentials or SDK, invalid LLM setting, refusal, `max_tokens` cut-off, instruction-like context | non-retryable `PlannerError` | straight to fallback |
| unparseable / malformed payload, invented hook | normaliser or claims issues -> `invalid_output` | repairer asked for a fresh plan |
| repair outside its decisions, purpose changed, same plan | decision guard issues | attempt rejected; `NO_PROGRESS` ends the run as `repair_failed` |
| budget would be exceeded | ledger refusal | `budget_exhausted` (terminal) |
| any other exception | programming error | re-raised, never disguised as an outage |

Fallbacks follow the cost sheet (`on_provider_unavailable:
use_replay_fixtures`): planner -> replay planner; repairer -> deterministic
repairer. Each switch is recorded in `RepairRun.fallbacks` and as a
`fallback` audit step.

## Audit trail

A `RepairRun` answers, from recorded data only:

| Question | Where |
|---|---|
| What evidence was available? | `evidence_fingerprint`, `pool` (eligible scenes/lines/music, rejected count), `context` |
| What did the planner propose? | `attempts[0].proposal`, `planner_mode`, `planner_version` |
| What failed? | `attempts[n].eligibility.violations`, `failing_clips`, `planning_issues`, `verification_issues` |
| Why did repair happen? | `attempts[n].decisions` (reason code, triggering violations, allowed actions, preserve list) |
| What changed / stayed? | `attempts[n].changes` (component-level, computed by comparing plans) |
| Who produced the repair? | `planner_mode`, `planner_version`, `usage.model` per attempt; `fallbacks` |
| Why accepted or rejected? | `status`, `verdict`, `final_eligibility`, `summary`, `audit_trail` (ends with `final_decision`) |
| What did it cost? | `budget` (calls, flat-rate cost, tokens, attempts, limits) and per-attempt `model_calls`, `usage`, `latency_seconds` |

## Budget controls

`BudgetLedger` is created per run from the cost sheet, optionally tightened
by run limits (`--max-model-calls`, `--max-cost` or their environment
variables; `config.run_limits` refuses values above the sheet before any
call). Before every call it checks the call and cost limits; a call that would
exceed either is not made and the run ends as `budget_exhausted` with no
accepted plan. Repair attempts are capped by `--max-attempts`. SDK retries
are disabled, so the only retries are the loop's, and each is checked
against the budget. Mock and deterministic stages make no model calls.
Limits on tool calls and media operations exist in the cost sheet but have
nothing to count yet.

## Configuration

`config.llm_settings` resolves model, effort and request timeout from CLI
flags, then `TRAILER_DIRECTOR_MODEL` / `TRAILER_DIRECTOR_EFFORT` /
`TRAILER_DIRECTOR_LLM_TIMEOUT_SECONDS`, then defaults (`claude-opus-5-5`,
`high`, 300 s). It is only called in `llm` mode, so offline modes never
depend on it; an invalid value becomes a non-retryable `PlannerError`
before any request.

## Multimodal model gateway

```
caller (models test today; planner/verifier when episode media exists)
   |  ModelRequest(capability, inputs)            capability: text | vision | audio | video
   v
media.py      validate inputs (exists, type matches capability), SHA-256 each file,
              request fingerprint = capability + model override + text + media hashes
   v
router.py     provider for the capability -> model configured? -> BudgetLedger.can_afford
   v
providers.py  GeminiProvider | MockProvider | ReplayProvider   (RecordingProvider wraps any)
   |          provider failure -> ModelUnavailableError; explicit fallback only, recorded
   v
ModelResponse -> ModelObservation (status model_observation | mock_output, authoritative: false)
```

- **Providers** implement `capabilities`, `model_for(request)` and
  `generate(request, records, fingerprint)`. `GeminiProvider` uses the
  `google-genai` SDK (`client.models.generate_content`, media sent inline via
  `Part.from_bytes`), a timeout from `TRAILER_DIRECTOR_LLM_TIMEOUT_SECONDS`
  and SDK retries off. Models come from `TRAILER_{TEXT,VISION,AUDIO,VIDEO}_MODEL`,
  with no defaults. 429/5xx/408/409, timeouts and connection errors are
  retryable; other API errors, missing key or model are not.
- **Budget**: the router shares `repair.budget.BudgetLedger` and
  `config.run_limits` with the repair loop (`--max-model-calls`,
  `--max-cost`); each gateway call is checked before it is made and charged
  at the `verifier_model_call` rate.
- **Record / replay**: `RecordingProvider` writes
  `<dir>/<request fingerprint>.json`; `ReplayProvider` returns it with
  `replayed: true` and no latency, so replay needs no key and no network.
- **Authority**: observations are evidence with uncertainty. Nothing in the
  gateway can change eligibility; the constraint engine does not read it.
- **Not wired yet**: the episode package has no media, so the planner and
  verifier do not call the gateway. When media is supplied, the integration
  points are a media manifest kept separate from `EpisodePackage` (to keep
  the evidence fingerprint stable), a verifier stage for media bounds and
  audio presence, and frame observations attached to each segment's evidence.

## Evaluation

`trailer_director.evaluation` runs 19 scenarios per applicable audience
(plus replayed live recordings) through the unchanged `RepairLoop`. A
scenario only injects a known problem into the audience's deterministic plan
or makes a stage unavailable, and states the designed behaviour as checks on
the run record (status, reason codes, components changed, fallbacks). Every
scenario runs twice; `replay_match` compares the records. The clock is fixed,
so the report is byte-reproducible. Metrics are per run with no aggregate
score. `evaluation.review` renders a human review sheet for creative
judgement, which the automated checks do not attempt.

## Why these parts are deterministic

- **Source truth.** IDs and timecodes either exist in the evidence or they do
  not. A planner that proposes `SC99` is stopped by the normaliser and would
  be stopped again by the engine.
- **Rights.** Licences are dates, territories, audiences and clauses with
  exact answers and legal risk. The rights rules read only the rights
  records, so no text - including text inside the evidence, or a planner's
  rationale - can widen them.
- **Timecodes.** Containment and ordering are integer comparisons.
- **Policy enforcement.** Spoiler ceilings and content limits are data;
  enforcing them in code makes the outcome repeatable and explainable instead
  of depending on how a model behaves on a given run.
- **Performance data.** Historical engagement is `info` in the engine and a
  small, labelled bonus in the mock planner. It never changes a verdict.
- **Planning and repair runs.** With the same evidence, context, planners (or
  replay files) and clock, a run produces the same attempts, result and run
  ID. Backoff sleeps are injectable so tests do not wait.
- **Repair decisions.** Which clips failed, what changed, and whether a repair
  is acceptable are computed from the plans and the engine result, so a
  repair planner cannot claim a fix it did not make.
- **Where the model is used.** Creative selection and wording - which
  eligible moments best serve an audience, the hook, the rationale - is
  where a model adds value. Truth, rights, policy and safety are decided by
  code the model cannot influence; even the model's repairs are untrusted
  and pass the same guard and engine.

## What is not built yet

A model-generated story map; a live evaluation of the LLM stages (one live
planner run per audience exists, no live repair run; creative quality is left to the
human review sheet);
media validation and source-reel mapping; observability beyond run records
and the audit trail; wardrobe and other non-prop continuity checks (free
text only in the dataset); token pricing (tokens are recorded, cost is the
flat cost-sheet rate); and tool-call and media-operation budgets (nothing
to count yet).
