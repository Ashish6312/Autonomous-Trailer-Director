"""Transitions, voice-over and text cards: defaults, derivation, verification, and the repository layout."""

import json

import pytest
from support import DATA_DIR, clip

from trailer_director.artifacts import build_trailer
from trailer_director.cli import main
from trailer_director.domain import AudienceType
from trailer_director.edit_fields import (
    EditFields,
    TextCard,
    Transition,
    VoiceOverLine,
    check_edit_fields,
    derive_edit_fields,
    load_edit_fields,
)
from trailer_director.planning import build_evidence_pool
from trailer_director.planning.pipeline import campaign_context

ROOT = DATA_DIR.parent
SAMPLE_RUN = ROOT / "sample_run"
LIVE_DIR = ROOT / "runs"
HOOK = "They have rotis, Maa. They just don't have you shouting at them."  # DLG_012
CLIPS = [
    clip("SC02", "00:01:02.000", "00:01:10.000", ["DLG_005"], clip_id="CLIP_001"),
    clip("SC02", "00:01:32.000", "00:01:43.000", ["DLG_009"], clip_id="CLIP_002"),
    clip("SC03", "00:02:14.000", "00:02:29.000", ["DLG_011", "DLG_012"], clip_id="CLIP_003"),
]


@pytest.fixture
def pool(evidence, engine):
    return build_evidence_pool(engine, evidence, campaign_context(evidence, AudienceType.FAMILY))


def _check(fields, evidence, pool):
    return check_edit_fields(fields, CLIPS, HOOK, evidence, pool)


def _card(text, sources, basis="dialogue"):
    return EditFields(
        text_cards=[TextCard(card_id="CARD_X", text=text, source_dialogue_ids=sources, basis=basis, duration_seconds=6)]
    )


def test_plans_without_the_fields_load_with_empty_lists():
    fields = load_edit_fields({"trailer_id": "TRL_OLD", "segments": []})

    assert (fields.transitions, fields.voice_over, fields.text_cards) == ([], [], [])


def test_derived_fields_are_valid_deterministic_and_invent_nothing(evidence, pool):
    first = derive_edit_fields(CLIPS, HOOK, ["DLG_012"])
    second = derive_edit_fields(CLIPS, HOOK, ["DLG_012"])

    assert first == second
    assert [(t.from_clip, t.to_clip, t.type) for t in first.transitions] == [
        ("CLIP_001", "CLIP_002", "cut"),
        ("CLIP_002", "CLIP_003", "dissolve"),
    ]
    assert first.voice_over == []
    assert [(c.text, c.source_dialogue_ids, c.basis) for c in first.text_cards] == [(HOOK, ["DLG_012"], "hook")]
    assert _check(first, evidence, pool) == []


def test_hook_without_a_source_gives_no_text_card():
    assert derive_edit_fields(CLIPS, "An invented slogan.", []).text_cards == []


@pytest.mark.parametrize(
    ("transition", "fragment"),
    [
        (Transition(from_clip="CLIP_001", to_clip="CLIP_009", type="cut", reason="x"), "unknown clip"),
        (Transition(from_clip="CLIP_001", to_clip="CLIP_003", type="cut", reason="x"), "not consecutive"),
        (Transition(from_clip="CLIP_002", to_clip="CLIP_001", type="cut", reason="x"), "not consecutive"),
        (Transition(from_clip="CLIP_001", to_clip="CLIP_002", type="star-wipe", reason="x"), "not allowed"),
    ],
)
def test_invalid_transitions_are_rejected(evidence, pool, transition, fragment):
    [problem] = _check(EditFields(transitions=[transition]), evidence, pool)

    assert fragment in problem


def test_empty_voice_over_is_valid(evidence, pool):
    assert _check(EditFields(voice_over=[]), evidence, pool) == []


def test_invented_voice_over_is_rejected(evidence, pool):
    invented = VoiceOverLine(text="This season, one family will be torn apart.", source_dialogue_ids=["DLG_012"])
    unknown_source = VoiceOverLine(text=HOOK, source_dialogue_ids=["DLG_999"])

    assert any("not in the episode dialogue" in p for p in _check(EditFields(voice_over=[invented]), evidence, pool))
    assert any("unknown source 'DLG_999'" in p for p in _check(EditFields(voice_over=[unknown_source]), evidence, pool))


def test_evidence_backed_text_card_is_valid(evidence, pool):
    assert _check(_card(HOOK, ["DLG_012"], basis="hook"), evidence, pool) == []


def test_unsupported_text_card_is_rejected(evidence, pool):
    problems = _check(_card("The secret that destroys a family.", ["DLG_012"]), evidence, pool)

    assert any("not in the episode dialogue" in p for p in problems)


def test_spoiler_text_card_is_rejected(evidence, pool):
    reveal = evidence.dialogue_line("DLG_042")  # the reveal, above the family spoiler ceiling

    problems = _check(_card(reveal.text, ["DLG_042"]), evidence, pool)

    assert any("may not use" in p for p in problems)


def test_hook_card_must_match_the_verified_hook(evidence, pool):
    other = evidence.dialogue_line("DLG_005").text

    assert any("differs from the verified hook" in p for p in _check(_card(other, ["DLG_005"], "hook"), evidence, pool))


@pytest.mark.parametrize("audience", list(AudienceType))
def test_committed_trailers_carry_valid_fields(evidence, engine, audience):
    trailer = json.loads((SAMPLE_RUN / f"{audience}_trailer.json").read_text(encoding="utf-8"))
    fields = load_edit_fields(trailer)
    clip_ids = [s["clip_id"] for s in trailer["segments"]]

    assert len(fields.transitions) == len(clip_ids) - 1
    assert fields.voice_over == []
    assert [c.text for c in fields.text_cards] == [trailer["hook"]["text"]]
    assert trailer["validation"]["edit_fields"] == {"status": "PASS", "problems": []}
    assert trailer == json.loads(json.dumps(build_trailer(evidence, audience, LIVE_DIR).edl))


def test_export_writes_the_sample_run_layout(tmp_path, capsys):
    out = tmp_path / "sample_run"

    assert main(["export", "--out", str(out), "--skip-evaluation", "--data-dir", str(DATA_DIR)]) == 0

    assert sorted(p.name for p in out.iterdir()) == [
        "constraint_map.json",
        "dialect_region_trailer.json",
        "family_trailer.json",
        "spoiler_map.json",
        "story_map.json",
        "validation_report.md",
        "young_adult_trailer.json",
    ]


def test_documents_and_sample_run_are_at_the_repository_root():
    docs = {"README.md", "ARCHITECTURE.md", "AI_COLLABORATION.md", "KNOWN_LIMITATIONS.md"}

    assert docs <= {p.name for p in ROOT.iterdir()}
    assert (SAMPLE_RUN / "validation_report.md").is_file()
