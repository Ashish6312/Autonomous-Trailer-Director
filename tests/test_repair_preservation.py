"""End to end: only the violating component changes; valid clips and every clip's purpose survive.

Also checks that the verification guard rejects repairs that change more
than their decisions allow, and that the audit trail tells the whole story.
"""

import json
from datetime import date

from support import FIXED_NOW, REPLAY_DIR

from trailer_director.constraints import ReasonCode
from trailer_director.domain import AudienceType
from trailer_director.planning import MockPlanner, PlannerMode, PlannerResponse, ReplayPlanner, RunStatus
from trailer_director.planning.pipeline import campaign_context
from trailer_director.planning.replay import load_replay
from trailer_director.repair import (
    AuditStep,
    ClipAction,
    Component,
    DeterministicRepairPlanner,
    LoopStatus,
    RepairLoop,
    RepairScope,
    VerificationCode,
)

FOOTAGE_FIELDS = ("clip_id", "scene_id", "source_in", "source_out", "dialogue_ids", "purpose")


class ScriptedPlanner:
    mode = PlannerMode.REPLAY
    version = "scripted"

    def __init__(self, payload):
        self._payload = payload

    def plan(self, story_map, strategy, pool):
        return PlannerResponse(mode=self.mode, planner_version=self.version, payload=self._payload)


class ScriptedRepairer(ScriptedPlanner):
    def repair(self, request):
        return PlannerResponse(mode=self.mode, planner_version=self.version, payload=self._payload(request))


def _loop(evidence, engine, repairer=None):
    return RepairLoop(evidence, engine, repairer or DeterministicRepairPlanner(evidence), sleep=lambda s: None)


def _ctx(evidence, on=None):
    return campaign_context(evidence, AudienceType.YOUNG_ADULT, date.fromisoformat(on) if on else None)


def _footage(clip):
    return {field: value for field, value in clip.model_dump(mode="json").items() if field in FOOTAGE_FIELDS}


def test_rights_change_changes_only_the_music(evidence, engine):
    loop = _loop(evidence, engine)
    before = loop.run(MockPlanner(evidence), _ctx(evidence, "2026-10-20"), FIXED_NOW).final_proposal

    run = loop.replan(before, _ctx(evidence, "2026-11-14"), FIXED_NOW)

    after = run.final_proposal
    assert run.status is LoopStatus.ACCEPTED
    assert [_footage(c) for c in after.candidate.clips] == [_footage(c) for c in before.candidate.clips]
    assert {c.music_id for c in before.candidate.clips} == {"MUS_03"}
    assert "MUS_03" not in {c.music_id for c in after.candidate.clips}
    repair = run.attempts[-1]
    assert {c.action for c in repair.changes} == {ClipAction.MUSIC_SWAPPED}
    assert all(c.changed == [Component.MUSIC] for c in repair.changes)
    assert {(d.scope, d.reason_code) for d in repair.decisions} == {
        (RepairScope.MUSIC, ReasonCode.PROMOTIONAL_RIGHTS_EXPIRED)
    }


def test_spoiler_repair_keeps_valid_clips_byte_for_byte_and_the_purpose(evidence, engine):
    run = _loop(evidence, engine).run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)

    initial = {c.clip_id: c for c in run.attempts[0].proposal.candidate.clips}
    final = {c.clip_id: c for c in run.final_proposal.candidate.clips}
    for clip_id in ("CLIP_001", "CLIP_002"):
        assert final[clip_id].model_dump_json() == initial[clip_id].model_dump_json()
    assert final["CLIP_003"].scene_id != "SC10"
    assert final["CLIP_003"].purpose == initial["CLIP_003"].purpose == "emotional_payoff"
    [decision] = run.attempts[-1].decisions
    assert (decision.clip_id, decision.reason_code) == ("CLIP_003", ReasonCode.SPOILER_LEVEL_EXCEEDED)


def test_misleading_line_is_recut_within_the_same_scene(evidence, engine):
    payload = json.loads(json.dumps(load_replay(REPLAY_DIR / "young_adult.json").response))
    payload["clips"] = payload["clips"][:2]
    payload["clips"][1].update(
        {
            "source_in": "00:08:24.500",
            "source_out": "00:08:30.500",
            "dialogue_ids": ["DLG_034"],
            "evidence": ["SC08", "DLG_034"],
        }
    )

    run = _loop(evidence, engine).run(ScriptedPlanner(payload), _ctx(evidence), FIXED_NOW)

    before = run.attempts[0].proposal.candidate.clips[1]
    after = next(c for c in run.final_proposal.candidate.clips if c.clip_id == before.clip_id)
    assert run.status is LoopStatus.ACCEPTED
    assert (after.scene_id, after.purpose, after.music_id) == (before.scene_id, before.purpose, before.music_id)
    assert "DLG_034" not in after.dialogue_ids
    change = next(c for c in run.attempts[-1].changes if c.clip_id == before.clip_id)
    assert change.action is ClipAction.RECUT
    assert set(change.changed) <= {Component.TIMECODES, Component.DIALOGUE}


def test_repair_that_changes_more_than_allowed_is_rejected(evidence, engine):
    loop = _loop(evidence, engine)
    before = loop.run(MockPlanner(evidence), _ctx(evidence, "2026-10-20"), FIXED_NOW).final_proposal

    def replace_scene_instead_of_music(request):
        payload = json.loads(json.dumps(_as_payload(request.proposal)))
        payload["clips"][0].update(
            {
                "scene_id": "SC06",
                "source_in": "00:05:44.500",
                "source_out": "00:05:50.500",
                "dialogue_ids": ["DLG_025"],
                "music_id": "MUS_02",
                "evidence": ["SC06", "DLG_025", "MUS_02"],
            }
        )
        return payload

    run = _loop(evidence, engine, ScriptedRepairer(replace_scene_instead_of_music)).replan(
        before, _ctx(evidence, "2026-11-14"), FIXED_NOW
    )

    repair = run.attempts[1]
    assert repair.status is RunStatus.INVALID_OUTPUT
    assert (VerificationCode.DECISION_VIOLATED, "CLIP_001") in {
        (i.code, i.location) for i in repair.verification_issues
    }


def test_repair_that_changes_a_purpose_is_rejected(evidence, engine):
    honest = DeterministicRepairPlanner(evidence)

    def rename_purpose(request):
        payload = honest.repair(request).payload
        payload["clips"][0]["purpose"] = "something_else"
        return payload

    run = _loop(evidence, engine, ScriptedRepairer(rename_purpose)).run(
        ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW
    )

    codes = {i.code for a in run.attempts for i in a.verification_issues}
    assert VerificationCode.PURPOSE_CHANGED in codes
    assert run.status is not LoopStatus.ACCEPTED


def test_audit_trail_tells_the_run_in_order(evidence, engine):
    run = _loop(evidence, engine).run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)

    assert [entry.step for entry in run.audit_trail] == [
        AuditStep.INITIAL_CANDIDATE,
        AuditStep.VERIFICATION,
        AuditStep.VIOLATION,
        AuditStep.VIOLATION,
        AuditStep.REPAIR_DECISION,
        AuditStep.CHANGE,
        AuditStep.CHANGE,
        AuditStep.REVERIFICATION,
        AuditStep.FINAL_DECISION,
    ]
    violation_refs = {ref for e in run.audit_trail if e.step is AuditStep.VIOLATION for ref in e.refs}
    decision_refs = {ref for e in run.audit_trail if e.step is AuditStep.REPAIR_DECISION for ref in e.refs}
    assert decision_refs <= violation_refs
    assert run.audit_trail[-1].detail == run.summary


def _as_payload(proposal):
    rationales = {r.clip_id: r for r in proposal.clip_rationales}
    return {
        "audience": proposal.audience,
        "trailer_id": proposal.candidate.trailer_id,
        "title": proposal.title,
        "hook": proposal.hook,
        "positioning": proposal.positioning,
        "rationale": proposal.rationale,
        "clips": [
            {
                **clip.model_dump(mode="json"),
                "reason": rationales[clip.clip_id].reason,
                "evidence": rationales[clip.clip_id].evidence_ids,
            }
            for clip in proposal.candidate.clips
        ],
    }
