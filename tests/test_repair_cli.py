import json
import time

from support import DATA_DIR

from trailer_director.cli import main

DATA = ["--data-dir", str(DATA_DIR)]


def test_repair_command_shows_selective_repair(capsys):
    code = main(["repair", "--audience", "young_adult", "--mode", "replay", *DATA])

    output = capsys.readouterr().out
    assert code == 0
    assert "violation         CLIP_003: SPOILER_001 SPOILER_LEVEL_EXCEEDED on scene SC10" in output
    assert "repair_decision   CLIP_003: replace_clip" in output
    assert "CLIP_003 replaced: changed scene, timecodes, dialogue (SC10 -> SC02)" in output
    assert "kept unchanged: CLIP_001, CLIP_002" in output
    assert "Budget: 1/24 model calls" in output


def test_repair_command_json(capsys):
    code = main(["repair", "--audience", "young_adult", "--mode", "replay", "--repair-mode", "replay", *DATA, "--json"])

    run = json.loads(capsys.readouterr().out)
    assert code == 0
    assert run["status"] == "accepted"
    assert run["budget"]["model_calls"] == 2


def test_repair_command_with_outage_uses_fallback(capsys, monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)

    code = main(["repair", "--audience", "family", "--simulate-outage", "5", *DATA, "--json"])

    run = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [a["kind"] for a in run["attempts"]] == ["initial", "retry", "retry", "fallback"]
    assert sleeps == [2.0, 4.0]


def test_repair_command_without_attempts_is_not_accepted(capsys):
    code = main(["repair", "--audience", "young_adult", "--mode", "replay", "--max-attempts", "0", *DATA])

    assert code == 1
    assert "attempts_exhausted" in capsys.readouterr().out


def test_replan_command_swaps_expired_music(capsys):
    code = main(["replan", "--audience", "young_adult", "--from-date", "2026-10-20", "--to-date", "2026-11-14", *DATA])

    output = capsys.readouterr().out
    assert code == 0
    assert output.count("PROMOTIONAL_RIGHTS_EXPIRED on music MUS_03") == 3
    assert output.count("replace_music (allowed: replace_music, remove_music; preserve: scene, timecodes") == 3
    assert output.count("music_swapped: changed music") == 3
