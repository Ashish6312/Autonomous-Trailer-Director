"""Engine-level behaviour: composition, determinism, severity semantics, extension and CLI."""

import json

from support import DATA_DIR, EXAMPLES_DIR, clip, context

from trailer_director.cli import EXIT_INVALID, EXIT_LOAD_ERROR, EXIT_OK, main
from trailer_director.constraints import (
    ConstraintEngine,
    ConstraintSeverity,
    ConstraintViolation,
    EligibilityResult,
    EntityType,
    ReasonCode,
)
from trailer_director.constraints.rules import DEFAULT_CLIP_RULES
from trailer_director.data import load_episode_package
from trailer_director.domain.edit import TrailerCandidate


def _load_candidate(name: str) -> TrailerCandidate:
    return TrailerCandidate.model_validate_json((EXAMPLES_DIR / name).read_text(encoding="utf-8"))


def test_all_violations_are_returned_together(engine):
    result = engine.evaluate_trailer(_load_candidate("young_adult_risky.json"), context("young_adult", on="2026-11-14"))

    found = {(v.clip_id, v.reason_code, v.entity_id) for v in result.errors}
    assert found >= {
        ("CLIP_001", ReasonCode.SPOILER_LEVEL_EXCEEDED, "SC10"),
        ("CLIP_001", ReasonCode.PROMOTIONAL_RIGHTS_EXPIRED, "MUS_03"),
        ("CLIP_002", ReasonCode.CONTRACT_RESTRICTION, "ACT_04"),
        ("CLIP_003", ReasonCode.AUDIENCE_NOT_LICENSED, "ACT_05"),
        ("CLIP_003", ReasonCode.TERRITORY_NOT_LICENSED, "ACT_05"),
    }
    assert not result.eligible


def test_sample_family_trailer_is_eligible(engine):
    result = engine.evaluate_trailer(_load_candidate("family_warmth.json"), context("family"))

    assert result.eligible
    assert result.errors == []
    assert [(w.reason_code, w.entity_id) for w in result.warnings] == [(ReasonCode.SUBTITLE_REVIEW_REQUIRED, "DLG_025")]


def test_same_input_gives_identical_output(engine):
    candidate = _load_candidate("young_adult_risky.json")
    ctx = context("young_adult", on="2026-11-14")

    first = engine.evaluate_trailer(candidate, ctx).model_dump_json()
    second = engine.evaluate_trailer(candidate, ctx).model_dump_json()
    fresh = ConstraintEngine(load_episode_package(DATA_DIR)).evaluate_trailer(candidate, ctx).model_dump_json()

    assert first == second == fresh


def test_any_error_makes_a_result_ineligible():
    def finding(severity: ConstraintSeverity) -> ConstraintViolation:
        return ConstraintViolation(
            rule_id="TEST_001",
            severity=severity,
            entity_type=EntityType.SCENE,
            entity_id="SC01",
            reason_code=ReasonCode.SCENE_NOT_FOUND,
            message="test",
        )

    def result(*severities: ConstraintSeverity) -> EligibilityResult:
        return EligibilityResult(
            subject_type=EntityType.SCENE,
            subject_id="SC01",
            context=context(),
            violations=[finding(s) for s in severities],
        )

    assert result(ConstraintSeverity.INFO, ConstraintSeverity.WARNING).eligible
    assert not result(ConstraintSeverity.INFO, ConstraintSeverity.ERROR).eligible


def test_new_rules_plug_in_without_engine_changes(evidence):
    class NoNightScenes:
        rule_id = "TEST_NIGHT_001"

        def evaluate(self, clip, context, evidence):
            scene = evidence.scenes.get(clip.scene_id)
            if scene is None or scene.time_of_day != "night":
                return []
            return [
                ConstraintViolation(
                    rule_id=self.rule_id,
                    severity=ConstraintSeverity.ERROR,
                    entity_type=EntityType.SCENE,
                    entity_id=scene.scene_id,
                    reason_code=ReasonCode.CONTENT_SEVERITY_EXCEEDED,
                    message="night scene",
                )
            ]

    engine = ConstraintEngine(evidence, clip_rules=(*DEFAULT_CLIP_RULES, NoNightScenes()))

    assert engine.evaluate_scene("SC11", context("young_adult")).eligible
    assert not engine.evaluate_clip(
        clip("SC08", "00:07:54.000", "00:08:00.000", ["DLG_031"]), context("young_adult")
    ).eligible


def test_cli_evaluates_sample_candidates(capsys):
    family = ["evaluate-candidate", "--candidate", str(EXAMPLES_DIR / "family_warmth.json"), "--audience", "family"]
    assert main([*family, "--data-dir", str(DATA_DIR), "--date", "2026-11-01"]) == EXIT_OK
    assert "ELIGIBLE: 0 error(s)" in capsys.readouterr().out

    music = ["evaluate-candidate", "--music", "MUS_03", "--audience", "young_adult", "--date", "2026-11-14"]
    assert main([*music, "--data-dir", str(DATA_DIR), "--json"]) == EXIT_INVALID
    payload = json.loads(capsys.readouterr().out)
    assert payload["eligible"] is False
    assert payload["violations"][0]["reason_code"] == "PROMOTIONAL_RIGHTS_EXPIRED"


def test_cli_rejects_malformed_candidate_file(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"trailer_id": "TRL_BAD", "clips": [{"clip_id": "CLIP_001"}]}), encoding="utf-8")

    code = main(["evaluate-candidate", "--candidate", str(bad), "--audience", "family", "--data-dir", str(DATA_DIR)])

    assert code == EXIT_LOAD_ERROR
    assert "invalid trailer candidate" in capsys.readouterr().err
