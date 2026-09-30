"""Rule descriptions for the constraint map. A test keeps this in sync with the engine's rule registry."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleInfo:
    rule_id: str
    category: str
    checks: str
    evidence: tuple[str, ...]
    reason_codes: tuple[str, ...]


RULE_CATALOG: tuple[RuleInfo, ...] = (
    RuleInfo(
        "SOURCE_SCENE_001",
        "source/timecode validity",
        "the clip's scene exists",
        ("episode/scenes.json",),
        ("SCENE_NOT_FOUND",),
    ),
    RuleInfo(
        "SOURCE_DIALOGUE_001",
        "source/timecode validity",
        "every declared line exists and belongs to the clip's scene",
        ("episode/dialogue.json",),
        ("DIALOGUE_NOT_FOUND", "DIALOGUE_SCENE_MISMATCH"),
    ),
    RuleInfo(
        "SOURCE_MUSIC_001",
        "source/timecode validity",
        "the music asset exists",
        ("rights/music.json",),
        ("MUSIC_NOT_FOUND",),
    ),
    RuleInfo(
        "TIMECODE_CLIP_001",
        "source/timecode validity",
        "source_in/source_out lie inside the scene",
        ("episode/scenes.json",),
        ("CLIP_STARTS_BEFORE_SCENE", "CLIP_ENDS_AFTER_SCENE"),
    ),
    RuleInfo(
        "TIMECODE_DIALOGUE_001",
        "source/timecode validity",
        "declared lines lie fully inside the cut; audible undeclared lines are flagged",
        ("episode/dialogue.json",),
        ("DIALOGUE_OUTSIDE_CLIP", "UNDECLARED_DIALOGUE_IN_CLIP"),
    ),
    RuleInfo(
        "SPOILER_001",
        "spoiler",
        "scene and line spoiler levels are within the audience's ceiling",
        ("episode/scenes.json", "episode/dialogue.json", "policies/rating_policies.json"),
        ("SPOILER_LEVEL_EXCEEDED",),
    ),
    RuleInfo(
        "RATING_CONTENT_001",
        "rating policy / audience safety",
        "sensitive content per category is within the audience's limit",
        ("episode/scenes.json", "episode/dialogue.json", "policies/rating_policies.json"),
        ("CONTENT_SEVERITY_EXCEEDED",),
    ),
    RuleInfo(
        "CONTEXT_MISLEADING_001",
        "story truth",
        "no line flagged as misleading out of context; flagged scenes need an editor's check",
        ("episode/scenes.json", "episode/dialogue.json"),
        ("MISLEADING_DIALOGUE_IN_CLIP", "MISLEADING_SCENE_CONTEXT"),
    ),
    RuleInfo(
        "STORY_TRUTH_RELATIONSHIP_001",
        "story truth / cultural",
        "a kinship word used to address someone matches the recorded relationship",
        ("episode/dialogue.json", "episode/characters.json", "episode/scenes.json"),
        ("MISLEADING_RELATIONSHIP",),
    ),
    RuleInfo(
        "RIGHTS_MUSIC_PROMO_001",
        "music rights / territory",
        "music is cleared for promotion, this audience, territory and date",
        ("rights/music.json",),
        (
            "PROMOTION_NOT_PERMITTED",
            "AUDIENCE_NOT_LICENSED",
            "TERRITORY_NOT_LICENSED",
            "RIGHTS_NOT_YET_ACTIVE",
            "PROMOTIONAL_RIGHTS_EXPIRED",
        ),
    ),
    RuleInfo(
        "RIGHTS_ACTOR_PROMO_001",
        "actor rights / territory",
        "every performer in the scene is cleared for this audience, territory and date",
        ("rights/actors.json", "episode/scenes.json"),
        (
            "ACTOR_RIGHTS_MISSING",
            "PROMOTION_NOT_PERMITTED",
            "AUDIENCE_NOT_LICENSED",
            "TERRITORY_NOT_LICENSED",
            "RIGHTS_NOT_YET_ACTIVE",
            "PROMOTIONAL_RIGHTS_EXPIRED",
        ),
    ),
    RuleInfo(
        "RIGHTS_ACTOR_RESTRICTION_001",
        "contracts / promotional restrictions",
        "contract clauses: prohibited scenes block, approval clauses need sign-off",
        ("rights/actors.json",),
        ("CONTRACT_RESTRICTION", "APPROVAL_REQUIRED"),
    ),
    RuleInfo(
        "ACCESSIBILITY_SUBTITLE_001",
        "accessibility",
        "lines marked not subtitle-safe are reviewed where subtitles are required",
        ("episode/dialogue.json", "policies/rating_policies.json"),
        ("SUBTITLE_REVIEW_REQUIRED",),
    ),
    RuleInfo(
        "ACCESSIBILITY_READING_SPEED_001",
        "accessibility",
        "subtitle reading speed above 17 characters per second is reviewed",
        ("episode/dialogue.json", "policies/rating_policies.json"),
        ("SUBTITLE_READING_SPEED_HIGH",),
    ),
    RuleInfo(
        "DIALECT_REVIEW_001",
        "cultural",
        "regional address terms are reviewed where the policy requires dialect review",
        ("episode/characters.json", "episode/dialogue.json", "policies/rating_policies.json"),
        ("DIALECT_REVIEW_REQUIRED",),
    ),
    RuleInfo(
        "PERFORMANCE_NOTE_001",
        "evidence only",
        "historical performance is attached as information; never affects a verdict",
        ("performance/historical_campaigns.json",),
        ("HISTORICAL_PERFORMANCE",),
    ),
    RuleInfo(
        "POLICY_DURATION_001",
        "rating policy",
        "total length is within the audience's maximum",
        ("policies/rating_policies.json",),
        ("TRAILER_TOO_LONG",),
    ),
    RuleInfo(
        "CONTINUITY_PROP_ORDER_001",
        "story truth / continuity",
        "tracked props are not cut against story order",
        ("episode/scenes.json", "episode/props.json"),
        ("CONTINUITY_ORDER_REVERSED",),
    ),
)

# Checks outside the engine that also decide whether a plan can ship.
PIPELINE_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("normaliser", "schema / unsupported references", "payload shape; every scene, line, music and evidence ID exists"),
    ("planning.claims", "story truth / misleading claims", "every hook sentence quotes a real line usable here"),
    ("repair.verification", "repair integrity", "a repair changes only what its decisions allow; purpose kept"),
    ("repair.budget", "budget", "model calls and estimated cost stay within the cost sheet and run limits"),
    ("llm.screening", "security", "instruction-like evidence text never reaches the model"),
    ("planning.strategy", "cultural / audience bias", "stereotyping profile preferences are withheld from planning"),
)
