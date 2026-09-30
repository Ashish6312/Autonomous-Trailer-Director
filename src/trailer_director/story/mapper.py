"""Deterministic baseline story mapper.

This is not natural-language story understanding. It reorganises the
structured evidence (scene functions, tones, spoiler levels, dialogue
importance, props, performance) into the story-map contract, so the planner
contract exists before a model-generated story map is introduced.
"""

from trailer_director.domain import (
    Dialogue,
    DramaticFunction,
    EpisodePackage,
    Importance,
    Scene,
    SpoilerLevel,
)
from trailer_director.story.models import (
    ArcPoint,
    EmotionalBeat,
    PerformanceSignal,
    Premise,
    ProtectedReveal,
    SceneFunction,
    StoryCharacter,
    StoryConflict,
    StoryEvent,
    StoryMap,
    StoryRelationship,
    StoryStake,
    TrailerHook,
)

PREMISE_FUNCTIONS = frozenset({DramaticFunction.SETUP, DramaticFunction.INCITING_INCIDENT})
PROTECTED_FROM = SpoilerLevel.MODERATE
HOOK_SPOILER_CEILING = SpoilerLevel.MINOR


def build_story_map(evidence: EpisodePackage) -> StoryMap:
    scenes = evidence.scenes_in_order()
    return StoryMap(
        episode_id=evidence.episode.episode_id,
        evidence_fingerprint=evidence.fingerprint(),
        method="deterministic_baseline",
        premise=Premise(
            logline=evidence.episode.spoiler_safe_logline,
            scene_ids=[s.scene_id for s in scenes if s.dramatic_function in PREMISE_FUNCTIONS],
        ),
        characters=_characters(evidence, scenes),
        relationships=_relationships(evidence, scenes),
        events=[_event(index, scene, evidence) for index, scene in enumerate(scenes, start=1)],
        emotional_beats=[
            EmotionalBeat(sequence=s.sequence, scene_id=s.scene_id, tones=s.emotional_tone) for s in scenes
        ],
        conflicts=_conflicts(evidence, scenes),
        stakes=_stakes(evidence, scenes),
        protected_reveals=_protected_reveals(evidence, scenes),
        trailer_hooks=_trailer_hooks(evidence, scenes),
        scene_functions=[_scene_function(scene, evidence) for scene in scenes],
    )


def _characters(evidence: EpisodePackage, scenes: list[Scene]) -> list[StoryCharacter]:
    story_characters = []
    for character_id in evidence.episode.character_ids:
        character = evidence.character(character_id)
        arc = [
            ArcPoint(scene_id=s.scene_id, dramatic_function=s.dramatic_function, emotional_tone=s.emotional_tone)
            for s in scenes
            if character_id in s.characters
        ]
        story_characters.append(
            StoryCharacter(
                character_id=character_id,
                name=character.name,
                role=character.role,
                description=character.description,
                arc=arc,
            )
        )
    return story_characters


def _relationships(evidence: EpisodePackage, scenes: list[Scene]) -> list[StoryRelationship]:
    return [
        StoryRelationship(
            character_id=character_id,
            related_character_id=relationship.character_id,
            relation=relationship.relation,
            note=relationship.note,
            shared_scene_ids=[
                s.scene_id for s in scenes if {character_id, relationship.character_id} <= set(s.characters)
            ],
        )
        for character_id in evidence.episode.character_ids
        for relationship in evidence.character(character_id).relationships
    ]


def _event(index: int, scene: Scene, evidence: EpisodePackage) -> StoryEvent:
    return StoryEvent(
        event_id=f"EVENT_{index:02d}",
        scene_id=scene.scene_id,
        description=scene.summary,
        dramatic_function=scene.dramatic_function,
        importance=scene.importance,
        spoiler_level=scene.spoiler_level,
        key_dialogue_ids=[line.dialogue_id for line in _high_importance_lines(scene, evidence)],
    )


def _conflicts(evidence: EpisodePackage, scenes: list[Scene]) -> list[StoryConflict]:
    conflict_scenes = [s for s in scenes if s.dramatic_function is DramaticFunction.CONFLICT]
    return [
        StoryConflict(
            conflict_id=f"CONFLICT_{index:02d}",
            scene_id=scene.scene_id,
            character_ids=scene.characters,
            description=scene.summary,
            dialogue_ids=[line.dialogue_id for line in _high_importance_lines(scene, evidence)],
        )
        for index, scene in enumerate(conflict_scenes, start=1)
    ]


def _stakes(evidence: EpisodePackage, scenes: list[Scene]) -> list[StoryStake]:
    return [
        StoryStake(
            prop_id=prop.prop_id,
            description=prop.description,
            scene_ids=[s.scene_id for s in scenes if any(a.prop_id == prop.prop_id for a in s.props)],
        )
        for prop in evidence.props.values()
        if prop.importance is Importance.HIGH
    ]


def _protected_reveals(evidence: EpisodePackage, scenes: list[Scene]) -> list[ProtectedReveal]:
    return [
        ProtectedReveal(
            scene_id=scene.scene_id,
            spoiler_level=scene.spoiler_level,
            dialogue_ids=[
                line.dialogue_id
                for line in evidence.lines_for_scene(scene.scene_id)
                if line.spoiler_level.rank >= PROTECTED_FROM.rank
            ],
        )
        for scene in scenes
        if scene.spoiler_level.rank >= PROTECTED_FROM.rank
    ]


def _trailer_hooks(evidence: EpisodePackage, scenes: list[Scene]) -> list[TrailerHook]:
    """High-importance lines that give away at most a minor spoiler; audience rules are applied later."""
    return [
        TrailerHook(
            dialogue_id=line.dialogue_id,
            scene_id=line.scene_id,
            speaker_id=line.speaker_id,
            text=line.text,
            tone=line.tone,
            spoiler_level=line.spoiler_level,
            subtitle_safe=line.subtitle_safe,
        )
        for scene in scenes
        for line in _high_importance_lines(scene, evidence)
        if line.spoiler_level.rank <= HOOK_SPOILER_CEILING.rank
    ]


def _scene_function(scene: Scene, evidence: EpisodePackage) -> SceneFunction:
    performance = sorted(
        (
            PerformanceSignal(audience=r.audience, campaign_id=r.campaign_id, engagement_score=r.engagement_score)
            for r in evidence.performance_for_scene(scene.scene_id)
        ),
        key=lambda signal: (signal.audience, signal.campaign_id),
    )
    return SceneFunction(
        scene_id=scene.scene_id,
        sequence=scene.sequence,
        dramatic_function=scene.dramatic_function,
        importance=scene.importance,
        spoiler_level=scene.spoiler_level,
        emotional_tone=scene.emotional_tone,
        character_ids=scene.characters,
        performance=performance,
    )


def _high_importance_lines(scene: Scene, evidence: EpisodePackage) -> list[Dialogue]:
    return [line for line in evidence.lines_for_scene(scene.scene_id) if line.importance is Importance.HIGH]
