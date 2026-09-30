# Autonomous Trailer Director

Plans three audience-specific trailers (family, young adult, dialect region)
from one episode package, with exact source timecodes and evidence for every
decision, while protecting story truth, viewer safety, cultural respect and
contractual rights.

A planner (Claude, a deterministic heuristic planner, or a recorded replay)
proposes edit decisions. Deterministic code checks every one of them against
the evidence and the rules, repairs only what failed, and decides the
verdict: `PASS`, `PASS_WITH_WARNINGS` or `REJECTED`.

Documents: [ARCHITECTURE.md](ARCHITECTURE.md) - [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) -
[AI_COLLABORATION.md](AI_COLLABORATION.md) - [docs/REFERENCE.md](docs/REFERENCE.md) (dataset, every
rule, design details) - [sample_run/](sample_run/) (generated plans and maps).

## Problem

Cutting one episode for several audiences means satisfying rules that are
easy to break: the family cut must not show the reveal or a shove; a licensed
track expires before the campaign airs; a contract bars a scene; a regional
honorific must not be subtitled as kinship; every clip must point at real
footage; and the run has a model budget. A language model is good at the
creative part and unreliable at the rest, and it can be steered by text
inside the evidence.

## Solution

```
episode package -> validation -> story map + spoiler map -> constraint engine (evidence pool per audience)
  -> audience strategy -> planner (mock | replay | Claude)            proposes; untrusted
  -> normaliser + hook-truth check -> constraint engine -> PASS / FAIL
  -> repair decisions (deterministic table) -> repairer carries them out -> decision guard -> engine
  -> final edit decision list + verdict + audit trail
```

The model sees only evidence the engine has already approved, never text
that reads like an instruction, and never decides eligibility.

## Input package

`data/` (validated by `trailer-director validate-data`):

| File | Content |
|---|---|
| `episode/episode.json`, `scenes.json`, `dialogue.json` | 12 scenes and 49 lines with `HH:MM:SS.mmm` timecodes, spoiler levels, sensitive content, subtitle safety |
| `episode/characters.json`, `locations.json`, `props.json` | identities, aliases (regional address terms), relationships, prop continuity |
| `rights/music.json`, `rights/actors.json` | promotion windows, audiences, territories, contract clauses |
| `policies/rating_policies.json` | per audience: spoiler ceiling, content limits, max length, subtitle / dialect review |
| `audiences/profiles.json` | positioning, themes, tones, avoid list, target length, cited evidence |
| `performance/historical_campaigns.json` | clip-test results (evidence only, never a verdict) |
| `economics/cost_sheet.json` | unit costs, run limits, retry and fallback policy |

## Output

`trailer-director export` writes to `sample_run/`:

- `story_map.json` - characters, relationships, events, emotional beats,
  conflicts, stakes, protected reveals, hooks, sensitive content
- `spoiler_map.json` - every scene and spoiler-bearing line, and whether each
  audience may use it
- `constraint_map.json` - every rule (what it checks, against which file,
  blocking vs review codes), policies, rights, budget, repair strategies
- `family_trailer.json`, `young_adult_trailer.json`,
  `dialect_region_trailer.json` - the edit decision lists
- `validation_report.md` - verdicts, promises, distinctness, segments,
  checks applied, human approvals, evaluation summary

Each trailer holds `trailer_id`, `audience`, `verdict`, `title`,
`audience_promise`, `hook` (with the dialogue line it quotes),
`emotional_journey`, `duration_seconds`, and per segment `source_in`,
`source_out`, `scene_id`, `video` (location, cast, visual tags), `audio`
(lines, music), `subtitles` (text, subtitle-safe, characters per second),
`reason`, `evidence` and `risk_flags`. Plus `transitions`, `voice_over`,
`text_cards` (see below), `validation`, `decision_log`,
`human_approvals`, `assumptions`, `estimated_cost` and `lower_cost_fallback`
(the deterministic plan, which makes 0 model calls).

Three plan-level fields are derived by rule after verification, not by the
planner, and checked by `edit_fields.check_edit_fields`:

- `transitions`: one per consecutive clip pair, `cut` within a scene and
  `dissolve` on a scene change, each with a reason. A suggestion for the
  editor; nothing is rendered.
- `voice_over`: always `[]`. The episode package has no voice-over script and
  none is invented. A line would have to quote usable dialogue to pass.
- `text_cards`: the verified hook as one end card (so the hook does not depend
  on audio), sized to be readable at 17 characters per second; otherwise `[]`.
  Card text must quote dialogue the audience may use, so it cannot add story
  facts or spoilers.

Plans written before these fields existed load with empty lists
(`edit_fields.load_edit_fields`).

| Audience | Planner source | Verdict | Length | Scenes |
|---|---|---|---|---|
| family | recorded live Claude plan | PASS | 44 s / 45 s target | SC02 x3, SC03 |
| young adult | recorded live Claude plan | PASS | 52 s / 60 s | SC02, SC03, SC05 x2 |
| dialect region | recorded live Claude plan | PASS_WITH_WARNINGS (8 dialect / subtitle review findings) | 56 s / 60 s | SC01, SC03, SC06, SC07 |

## Setup

Python 3.12+.

```bash
uv venv .venv --python 3.12
uv pip install --python .venv -e ".[dev]"   # or: python -m pip install -e ".[dev]"
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
```

No API key is needed for mock mode, replay mode, the tests, the evaluation
or the export.

## Configuration

| Setting | Flag | Environment | Default |
|---|---|---|---|
| model | `--model` | `TRAILER_DIRECTOR_MODEL` | `claude-opus-5-5` |
| effort | `--effort` | `TRAILER_DIRECTOR_EFFORT` | `high` |
| request timeout (s) | - | `TRAILER_DIRECTOR_LLM_TIMEOUT_SECONDS` | 300 |
| max model calls | `--max-model-calls` | `TRAILER_DIRECTOR_MAX_MODEL_CALLS` | cost sheet (24) |
| max estimated cost | `--max-cost` | `TRAILER_DIRECTOR_MAX_COST` | cost sheet (1.50 USD) |
| fallback on provider outage | `--no-fallback` to disable | - | cost sheet (`use_replay_fixtures`) |
| credentials | - | `ANTHROPIC_API_KEY` or `ant auth login` | none |
| gateway models (per capability) | `models test --model` | `TRAILER_TEXT_MODEL`, `TRAILER_VISION_MODEL`, `TRAILER_AUDIO_MODEL`, `TRAILER_VIDEO_MODEL` | none (unset is reported, not guessed) |
| gateway credentials | - | `GEMINI_API_KEY` | none |

Run limits can only tighten the cost sheet. Invalid values stop the command
(exit 2) before any API call; LLM settings are only read in `llm` mode.

## CLI usage

```bash
trailer-director validate-data
trailer-director evaluate-candidate --scene SC10 --audience family
trailer-director plan --audience family --mode mock
trailer-director repair --audience young_adult --mode replay
trailer-director replan --audience young_adult --from-date 2026-10-20
trailer-director evaluate --out evaluation/results.json --review-sheet evaluation/human_review.md
trailer-director export                      # writes sample_run/
```

Exit codes: `0` success / eligible / accepted, `1` not eligible / rejected /
an unexpected evaluation result, `2` load or configuration error.

### Modes

| Mode | Planner / repairer | Needs a key | Deterministic |
|---|---|---|---|
| `mock` (default) | heuristic planner, deterministic repairer | no | yes |
| `replay` | recorded responses (`--replay-dir`, `--repair-replay-dir`) | no | yes |
| `llm` | Claude; `--record-dir DIR` saves responses as replay files | yes | no (record, then replay) |

The default replay directory `examples/planner_runs` holds hand-authored
fixtures. The recorded live Claude plans are in `runs/planner_runs`:

```bash
trailer-director repair --audience young_adult --mode replay --replay-dir runs/planner_runs
# live (paid; about 1 planner call and up to 3 repair calls):
trailer-director repair --audience young_adult --mode llm --repair-mode llm --record-dir runs --json
```

### Example run (replay of the recorded live young-adult plan)

```
Repair run REPAIR_37e5d45e0eae | audience=young_adult date=2026-11-14 territory=IN
Audit trail:
  #0  initial_candidate TRL_AAKHRI_CHITTHI_YA_01 from replay llm-planner/claude-opus-5-5/high: CLIP_001 SC02, CLIP_002 SC03, CLIP_003 SC05, CLIP_004 SC05
  #0  verification      eligible: 0 error(s), 0 warning(s)
  -   final_decision    accepted after 0 repair attempt(s)
Result: PASS - accepted after 0 repair attempt(s)
Budget: 1/24 model calls, 0.045/1.50 USD, 0/3 repair attempts
Tokens: 9056 in, 3012 out (actual; cost above is the flat rate)
Final candidate: TRL_AAKHRI_CHITTHI_YA_01 | hook: "If I told you, you'd hate someone else instead of me. I chose me."
  CLIP_001 SC02 00:01:10.000-00:01:25.000 lines DLG_006, DLG_007, DLG_008 music -
  ...
```

## Verification

Enforced in code, never in a prompt. The full catalogue is in
`sample_run/constraint_map.json` and [docs/REFERENCE.md](docs/REFERENCE.md).

| Area | Check |
|---|---|
| sources and timecodes | scene / line / music exist; cut inside the scene; declared lines inside the cut; invented IDs rejected by the normaliser |
| spoilers | scene and line spoiler level within the audience ceiling; protected reveals never sent to the model |
| story truth | lines flagged misleading out of context; kinship words that contradict recorded relationships; hook must quote real, usable dialogue; prop continuity |
| rating and audience safety | per-category content limits; trailer length |
| rights | music and performer windows by audience, territory and date; contract clauses (prohibit / requires approval) |
| cultural | regional address terms go to dialect review; stereotyping audience preferences withheld from planning |
| accessibility | not-subtitle-safe lines; reading speed above 17 characters per second |
| repair integrity | a repair may change only the components its decisions allow; purpose kept; no-progress detected |
| budget | model calls and estimated cost checked before every call |

`error` blocks. `warning` means a person must review, and it is never auto-repaired. `info` (historical
performance) never affects a verdict.

## Repair and replanning

When verification fails, the loop identifies each failing clip and the rules
it broke. It maps each violation to allowed actions (replace music, re-cut in
scene, replace clip, drop), asks the repairer to carry out only those, checks
that nothing else changed, and re-verifies. If no safe repair works within
the attempts and budget, the plan is `REJECTED`; nothing invalid is forced
through. `replan` re-verifies an accepted plan under changed conditions and
repairs only what broke.

| Change | Behaviour | Evaluation scenario |
|---|---|---|
| best-performing scene is a spoiler (SC10) | rejected; that clip replaced; others kept | S02 |
| music rights expire | music swapped; every shot kept | S03, S10 |
| planner proposes a nonexistent scene | invalid output; fresh plan | S04 |
| marketing asks for a clickbait hook | `HOOK_NOT_IN_EVIDENCE`; never ships | S16 |
| subtitle turns honorific "Kaka" into "Uncle" | `MISLEADING_RELATIONSHIP`; that line re-cut out | S18 |
| source text says to ignore a contract | never reaches the model; a plan obeying it is rejected (`CONTRACT_RESTRICTION`) | S06 |
| audience data contains a stereotype | withheld from the strategy and the model; flagged for cultural review | S19 |
| contract amended after approval | only the affected clip replaced | S17 |
| preferred model unavailable | retries with backoff, then replay fallback (planner) or deterministic fallback (repairer) | S07, S08 |

## Multimodal model gateway

`trailer_director.multimodal` gives the system access to text, vision, audio
and video models through one capability-based interface. Callers ask for a
capability (`ModelRequest`), not a provider. The router picks the provider,
validates inputs, checks the run budget before the call, charges it after,
and uses a fallback only when one is explicitly configured, recording it.

| Provider | Capabilities | Use |
|---|---|---|
| Gemini (`google-genai`, `pip install -e ".[multimodal]"`) | text, vision, audio, video (inline media up to 20 MB) | live |
| mock | all, labelled `mock_output` | tests |
| replay | all | recorded responses, no API call |
| Claude | text | trailer planner/repairer only (`llm/`), not routed through the gateway |

Media inputs are identified by SHA-256; recordings (`--record-dir`, one JSON
file per request fingerprint) keep the fingerprint, file name, type and size,
never the bytes or credentials. Model output becomes a `ModelObservation`
with `authoritative: false`; it never overrides metadata, rights, policy or
timecodes.

```bash
trailer-director models list
trailer-director models capabilities
trailer-director models test --capability vision                         # live Gemini; needs key + TRAILER_VISION_MODEL
trailer-director models test --capability vision --record-dir runs/model_calls
trailer-director models test --capability vision --mode replay          # 0 API calls
trailer-director models test --capability audio --mode mock
```

`models test` sends a synthetic test fixture (`tests/fixtures/media/`, not
episode media) unless `--input PATH` is given. One live smoke test exists:
vision with `gemini-3.8-flash` on the test image (2026-09-30 13:23 UTC,
1,101 / 28 tokens, 6.8 s), recorded in `runs/model_calls/` and replayed with
no API call. Audio and video have been tested only with a fake client. **The episode package contains
no video or audio, so no episode media has been analysed;** the planner and
verifier do not use the gateway yet.

## Testing

```bash
python -m pytest              # 417 tests, offline
uvx ruff check . && uvx ruff format --check .
```

Covered: each rule category, missing scene, malformed timecode, out-of-scene
cut, music, actor, territory and contract rights, spoilers, rating, misleading
lines, clickbait hooks, relationship truth, prompt injection in evidence,
contract and date changes, model unavailable, timeout, budget limits,
invalid configuration, a nonexistent scene proposed by the model, credential
leakage, replay of the live recordings, evaluation reproducibility, and
artifact export. Mock, replay, evaluation and export are tested with sockets
blocked.

## Evaluation

`trailer-director evaluate` runs 19 scenarios for every audience they apply
to, plus a replay of each live recording (L01). Each scenario runs twice from
scratch. The latest run: 57 runs across 20 scenario IDs, all behaving as
designed, all reproducible, and the report is byte-identical between
executions. Three scenario/audience pairs cannot be set up from this dataset
and are reported as not applicable, with the reason. Per run it records
statuses, reason codes, repair attempts, planner and repair calls, fallbacks,
changed and preserved components (`CLIP_00n.component`), estimated cost and
tokens. **There is no aggregate score.** Creative quality is judged by people
on `evaluation/human_review.md`.

## Security

- The model context contains only engine-approved evidence. Protected-reveal
  content, production and continuity notes, and the synopsis are never sent.
  Evidence text that reads like an instruction is withheld and logged
  (`EVIDENCE_WITHHELD`); if any remains, the request is not sent.
- The system prompt marks all evidence as data. Model output is untrusted and
  re-verified.
- Credentials are resolved by the Anthropic SDK and never read, logged or
  recorded by project code. A test plants a fake key and checks outputs and
  recordings.

## Observability

Every run record (`--json`) contains: run ID, evidence fingerprint, context,
evidence pool summary, and every attempt. Each attempt records its planner
mode and version, proposal, violations, repair decisions, component-level
changes, tokens, serving model and latency. The record also holds fallbacks,
budget (calls, flat-rate cost, tokens, limits), the audit trail, status and
verdict. Estimated cost is the cost sheet's flat rate, not billing.

## Human approvals

Each trailer lists its required approvals (`human_approvals`):

| Role | Always | Also when |
|---|---|---|
| editorial | final cut sign-off (no media was inspected) | continuity, misleading-scene, undeclared-line or reading-speed findings |
| legal | confirm rights and contract records are current | a clause requires performer approval |
| cultural | dialect-region trailer | dialect or subtitle review findings; stereotyping preferences were withheld |
| marketing | title, audience promise and hook | - |

## Live-run status

| Run | Result |
|---|---|
| family, `plan --mode llm` (2026-09-30 07:05 UTC) | eligible first time; 44 s; replay-verified; recording predates token and latency fields |
| young adult, `repair --mode llm --repair-mode llm` (10:44 UTC) | 9,056 input / 3,012 output tokens, 32.0 s. Eligible first time, so **no repair was requested** and no repair recording exists. Replay-verified: identical plan, 0 errors, 0 warnings, fingerprint `f855637bbf6a9b74` |
| dialect region, `--mode llm --record-dir runs` (11:37 UTC) | 10,602 input / 3,157 output tokens, 32.0 s. Eligible first time (0 repair attempts, no fallback). Replay-verified: `PASS_WITH_WARNINGS`, 4 clips (SC01, SC03, SC06, SC07), 56 s of a 60 s target, 0 errors, 8 review findings (regional address terms Kaka, Maa, Papa, Chacha, Munna and three not-subtitle-safe lines), sent to cultural review |
| live repair | **never executed** |

The three trailers share little: family and dialect region both use the
SC03 roti exchange (DLG_011, DLG_012); otherwise scenes and lines differ.
All three live calls show 0.045 USD, the cost sheet's flat rate, not billing. To
exercise the live repairer on a plan that needs repair (the fixture carries
the SC10 spoiler), record to a separate directory:
`repair --audience young_adult --mode replay --repair-mode llm --record-dir runs/live_repair`.

## Status

The architecture is production-oriented. It is not production ready: there is
no media pipeline, the data is one synthetic episode, and live model
behaviour is partially observed. See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).
