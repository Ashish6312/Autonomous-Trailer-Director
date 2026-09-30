import copy

import pytest

from trailer_director.domain import AudienceType, ValidationSeverity
from trailer_director.planning import IssueCode, normalize_planner_output

VALID_PAYLOAD = {
    "audience": "family",
    "trailer_id": "TRL_TEST",
    "title": "Coming Home",
    "hook": "They have rotis, Maa.",
    "positioning": "Warm homecoming.",
    "rationale": "Lead with warmth.",
    "clips": [
        {
            "clip_id": "CLIP_001",
            "scene_id": "SC03",
            "source_in": "00:02:17.500",
            "source_out": "00:02:27.500",
            "dialogue_ids": ["DLG_011", "DLG_012"],
            "music_id": "MUS_02",
            "purpose": "mother_daughter_humour",
            "reason": "Kitchen banter in SC03 (DLG_012) shows the mother-daughter bond.",
            "evidence": ["SC03", "DLG_012", "CMP_PRELAUNCH_CLIPS"],
        }
    ],
}


@pytest.fixture
def payload():
    return copy.deepcopy(VALID_PAYLOAD)


def _normalize(payload, evidence):
    return normalize_planner_output(payload, evidence, AudienceType.FAMILY)


def _codes(issues):
    return {(issue.code, issue.location) for issue in issues if issue.severity is ValidationSeverity.ERROR}


def test_valid_output_is_accepted(payload, evidence):
    proposal, issues = _normalize(payload, evidence)

    assert issues == []
    assert proposal.candidate.clips[0].dialogue_ids == ["DLG_011", "DLG_012"]
    assert proposal.clip_rationales[0].evidence_ids == ["SC03", "DLG_012", "CMP_PRELAUNCH_CLIPS"]


def test_unknown_scene_is_rejected_cleanly(payload, evidence):
    payload["clips"][0]["scene_id"] = "SC99"

    proposal, issues = _normalize(payload, evidence)

    assert proposal is None
    assert (IssueCode.UNKNOWN_SCENE, "output.clips[0].scene_id") in _codes(issues)


def test_missing_scene_id_is_rejected_cleanly(payload, evidence):
    del payload["clips"][0]["scene_id"]

    proposal, issues = _normalize(payload, evidence)

    assert proposal is None
    assert (IssueCode.INVALID_CLIP, "output.clips[0].scene_id") in _codes(issues)


def test_malformed_timecode_is_rejected_cleanly(payload, evidence):
    payload["clips"][0]["source_in"] = "00:61:00.000"

    proposal, issues = _normalize(payload, evidence)

    assert proposal is None
    assert (IssueCode.INVALID_CLIP, "output.clips[0].source_in") in _codes(issues)


def test_unknown_dialogue_is_rejected_cleanly(payload, evidence):
    payload["clips"][0]["dialogue_ids"].append("DLG_999")

    _, issues = _normalize(payload, evidence)

    assert (IssueCode.UNKNOWN_DIALOGUE, "output.clips[0].dialogue_ids[2]") in _codes(issues)


def test_unknown_music_and_evidence_are_rejected(payload, evidence):
    payload["clips"][0]["music_id"] = "MUS_99"
    payload["clips"][0]["evidence"].append("CMP_INVENTED")

    _, issues = _normalize(payload, evidence)

    assert {
        (IssueCode.UNKNOWN_MUSIC, "output.clips[0].music_id"),
        (IssueCode.UNKNOWN_EVIDENCE, "output.clips[0].evidence[3]"),
    } <= _codes(issues)


@pytest.mark.parametrize(
    ("value", "code"), [(None, IssueCode.MISSING_FIELD), ("young_adult", IssueCode.AUDIENCE_MISMATCH)]
)
def test_missing_or_wrong_audience_is_rejected(payload, evidence, value, code):
    if value is None:
        del payload["audience"]
    else:
        payload["audience"] = value

    proposal, issues = _normalize(payload, evidence)

    assert proposal is None
    assert (code, "output.audience") in _codes(issues)


def test_clip_without_reason_or_evidence_is_rejected(payload, evidence):
    del payload["clips"][0]["reason"]
    payload["clips"][0]["evidence"] = []

    _, issues = _normalize(payload, evidence)

    assert {code for code, _ in _codes(issues)} == {IssueCode.MISSING_RATIONALE}


@pytest.mark.parametrize("raw", [None, "not json", ["clips"], {"clips": "nope"}, {"clips": [42]}])
def test_malformed_structures_never_raise(raw, evidence):
    proposal, issues = _normalize(raw, evidence)

    assert proposal is None
    assert issues and all(issue.severity is ValidationSeverity.ERROR for issue in issues)


def test_duplicate_clip_ids_are_rejected(payload, evidence):
    payload["clips"].append(copy.deepcopy(payload["clips"][0]))

    proposal, issues = _normalize(payload, evidence)

    assert proposal is None
    assert {code for code, _ in _codes(issues)} == {IssueCode.MALFORMED_OUTPUT}


def test_unknown_clip_field_is_a_warning(payload, evidence):
    payload["clips"][0]["confidence"] = 0.9

    proposal, issues = _normalize(payload, evidence)

    assert proposal is not None
    assert [(i.severity, i.location) for i in issues] == [(ValidationSeverity.WARNING, "output.clips[0].confidence")]


def test_all_problems_are_reported_together(payload, evidence):
    payload["audience"] = "young_adult"
    payload["clips"][0]["scene_id"] = "SC99"
    payload["clips"][0]["dialogue_ids"] = ["DLG_999"]
    del payload["title"]

    _, issues = _normalize(payload, evidence)

    assert {code for code, _ in _codes(issues)} == {
        IssueCode.AUDIENCE_MISMATCH,
        IssueCode.MISSING_FIELD,
        IssueCode.UNKNOWN_SCENE,
        IssueCode.UNKNOWN_DIALOGUE,
    }
