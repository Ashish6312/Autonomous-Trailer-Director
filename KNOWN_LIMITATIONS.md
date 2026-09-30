# Known limitations

What the system does not do, or does only approximately. A `PASS` verdict
means "passed deterministic verification", not "approved": every plan still
needs the human approvals listed in the README.

## Evidence and media

- **The project has multimodal model capability, but the supplied Aakhri
  Chitthi episode package does not include actual episode video/audio.
  Therefore no episode-level visual/audio claims are made.** The model
  gateway (`trailer_director.multimodal`, Gemini for text, image, audio and
  video) has been exercised with a fake client in tests and with synthetic
  test fixtures (`tests/fixtures/media/`). One live call has been made: vision
  on the test image with `gemini-3.8-flash`, recorded in `runs/model_calls/`
  and replayed. Audio and video have not been called live. The planner and verifier do
  not consume model observations yet, and subtitle/audio alignment is not
  verified. Human visual review remains required.
- **No video is rendered.** The output is an edit decision list; transitions
  are suggestions and no trailer file is produced.
- **No media is analysed.** There are no video or audio files. Eligibility is
  judged from scene and line metadata (spoiler levels, content flags, cast,
  timecodes); scene-level flags apply to the whole scene. There is no shot
  detection, render, loudness or frame check, and no source-reel mapping
  (timecodes are episode-relative). The `video` and `audio` blocks of each
  segment describe the evidence, not inspected media.
- **One synthetic episode.** Audience profiles, performance numbers and costs
  are invented for this project and labelled as such.
- **Subtitles are the dialogue text.** There is no separate subtitle track or
  original-language transcript; the kinship readings have not been checked by
  a regional language consultant.
- **Known data finding, kept on purpose.** Relationship reciprocity is checked:
  CHAR_BANSI lists CHAR_SHANKAR as an old friend, but CHAR_SHANKAR does not
  list CHAR_BANSI. The data is unchanged because fixing it changes the
  evidence fingerprint (`f855637bbf6a9b74`) that both live recordings were
  made against. `validate-data` reports 0 errors and this 1 warning; a test
  pins it.

## Checks that are approximate

- **Subtitle reading speed** divides a line's characters by its spoken
  duration and flags more than 17 characters per second. The threshold is a
  common subtitling guideline, not a value in the dataset's policies.
- **Relationship truth** checks English kinship words used as a form of
  address ("..., Uncle.") against the speaker's recorded relationships with
  the named character or, if none is named, everyone else in the scene. It
  does not interpret regional terms themselves (that is the dialect review),
  third-person references ("your father"), or sarcasm.
- **Hook truth** requires every sentence of the hook to quote a real line the
  audience may use. Title and audience promise are free marketing copy and
  are not checked mechanically. A plan with an invented hook is discarded and
  the repairer asked for a new plan; the hook is not repaired in place.
- **Instruction screening** (evidence text that reads like a command) and the
  **audience-bias guard** (stereotyping phrases in audience profiles) are
  narrow pattern lists, not classifiers. The system prompt, the filtered
  context and downstream verification remain the main defences.
- **Wardrobe and other non-prop continuity** exist only as free-text
  `continuity_notes`, so they are not checked; prop continuity (structured)
  is.
- **Cultural authenticity is not automated.** The dialect rule finds regional
  address terms where review is required; whether a term, custom or subtitle
  is accurate and respectful is decided by a regional reviewer.

## Planning and model use

- **Live model behaviour is partially observed.** One live Claude planner
  call per audience exists (`runs/planner_runs/`), each eligible on the first
  attempt. The **live repair path has never executed**, and run-to-run
  variance and prompt quality are unmeasured (one sample per audience). The
  family recording predates the token and latency fields.
- The deterministic mock planner, used as the lower-cost fallback, is
  conservative: one short clip per scene, well under the target length
  (23 s of 60 s for the dialect region).
- `examples/planner_runs` and `examples/repair_runs` are hand-authored
  fixtures, not model output. The dialect-region fixture's hook quotes a real
  line that is not in its cut (reported as a warning).
- Repair decisions are rule-based. "Creative purpose" is preserved as the
  clip's `purpose` label plus a preference for the same dramatic function,
  not as a judgement of the new footage.
- The story map is a deterministic reorganisation of metadata; a
  model-generated story map is not built.
- The evaluation injects known problems into deterministic plans and
  checks behaviour. It does not measure creative quality; that is the human
  review sheet (`evaluation/human_review.md`).

## Cost and operations

- **Costs are estimates.** The budget charges the cost sheet's flat per-call
  rates (synthetic, "not vendor pricing"). Actual input/output tokens and the
  serving model are recorded but not priced. Failed provider calls are not
  charged. A server-side refusal fallback could bill a second model inside
  one call; the serving model is recorded so this is visible.
- Gateway calls are charged at the cost sheet's `verifier_model_call` rate
  (0.015 USD), the closest existing item; the sheet has no multimodal item.
  Media over 20 MB would need the Gemini Files API, which is not implemented.
- **Tool-call and media-operation limits are not enforced**: the system makes
  no tool calls or media operations yet. Model calls, estimated cost and
  repair attempts are enforced.
- The CLI's retry backoff sleeps for real (2 s, 4 s); tests and the evaluation
  inject a no-op sleep.
- No observability beyond the JSON run records and audit trail (no metrics
  store or central logs); no job queue or resume after a crash.
