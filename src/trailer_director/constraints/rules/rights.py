"""Music and performer rights, enforced from the rights records only.

Free text in the evidence (summaries, notes, production notes) is never
read here, so it cannot widen what a contract allows.
"""

from trailer_director.constraints.models import (
    ConstraintContext,
    ConstraintSeverity,
    ConstraintViolation,
    EntityType,
    ReasonCode,
)
from trailer_director.domain import EpisodePackage, PromotionalUse, RestrictionEffect
from trailer_director.domain.edit import TrailerClip
from trailer_director.domain.rights import RightsWindow

MUSIC_RULE_ID = "RIGHTS_MUSIC_PROMO_001"
ACTOR_RULE_ID = "RIGHTS_ACTOR_PROMO_001"
RESTRICTION_RULE_ID = "RIGHTS_ACTOR_RESTRICTION_001"

_MUSIC_REMEDIATION = "Replace the music asset with an eligible promotional track."
_ACTOR_REMEDIATION = "Use a scene without this performer, or change the campaign audience, territory or date."


def music_rights_violations(
    music_id: str, context: ConstraintContext, evidence: EpisodePackage, clip_id: str | None = None
) -> list[ConstraintViolation]:
    asset = evidence.music.get(music_id)
    if asset is None:
        return []
    if not asset.allowed_for_promotion:
        return [
            ConstraintViolation(
                rule_id=MUSIC_RULE_ID,
                severity=ConstraintSeverity.ERROR,
                entity_type=EntityType.MUSIC,
                entity_id=music_id,
                reason_code=ReasonCode.PROMOTION_NOT_PERMITTED,
                message=f"Music asset {music_id} is not cleared for promotional use at all.",
                remediation=_MUSIC_REMEDIATION,
                clip_id=clip_id,
            )
        ]
    return _window_violations(
        MUSIC_RULE_ID,
        asset,
        EntityType.MUSIC,
        music_id,
        f"Music asset {music_id}",
        context,
        clip_id,
        _MUSIC_REMEDIATION,
    )


class MusicRightsRule:
    rule_id = MUSIC_RULE_ID

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        if clip.music_id is None:
            return []
        return music_rights_violations(clip.music_id, context, evidence, clip.clip_id)


class ActorRightsRule:
    """Every character present in the scene may be in frame, so every performer must be cleared."""

    rule_id = ACTOR_RULE_ID

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        scene = evidence.scenes.get(clip.scene_id)
        if scene is None:
            return []
        violations = []
        for character_id in scene.characters:
            rights = evidence.find_actor_rights(character_id)
            if rights is None:
                violations.append(self._missing(character_id, clip.clip_id))
            elif rights.promotional_use is PromotionalUse.PROHIBITED:
                violations.append(self._prohibited(rights.actor_id, character_id, clip.clip_id))
            else:
                label = f"Performer {rights.actor_id} ({character_id})"
                violations += _window_violations(
                    self.rule_id,
                    rights,
                    EntityType.ACTOR,
                    rights.actor_id,
                    label,
                    context,
                    clip.clip_id,
                    _ACTOR_REMEDIATION,
                )
        return violations

    def _missing(self, character_id: str, clip_id: str) -> ConstraintViolation:
        return ConstraintViolation(
            rule_id=self.rule_id,
            severity=ConstraintSeverity.ERROR,
            entity_type=EntityType.CHARACTER,
            entity_id=character_id,
            reason_code=ReasonCode.ACTOR_RIGHTS_MISSING,
            message=f"No performer rights record covers {character_id}.",
            remediation=_ACTOR_REMEDIATION,
            clip_id=clip_id,
        )

    def _prohibited(self, actor_id: str, character_id: str, clip_id: str) -> ConstraintViolation:
        return ConstraintViolation(
            rule_id=self.rule_id,
            severity=ConstraintSeverity.ERROR,
            entity_type=EntityType.ACTOR,
            entity_id=actor_id,
            reason_code=ReasonCode.PROMOTION_NOT_PERMITTED,
            message=f"Performer {actor_id} ({character_id}) may not appear in promotional material.",
            remediation=_ACTOR_REMEDIATION,
            clip_id=clip_id,
            details={"character_id": character_id},
        )


class ContractRestrictionRule:
    """Scene-specific contract clauses. Clauses without scene IDs are expressed by the rights window instead."""

    rule_id = RESTRICTION_RULE_ID

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        scene = evidence.scenes.get(clip.scene_id)
        if scene is None:
            return []
        violations = []
        for character_id in scene.characters:
            rights = evidence.find_actor_rights(character_id)
            if rights is None:
                continue
            for restriction in rights.restrictions:
                if clip.scene_id not in restriction.scene_ids:
                    continue
                blocking = restriction.effect is RestrictionEffect.PROHIBIT
                violations.append(
                    ConstraintViolation(
                        rule_id=self.rule_id,
                        severity=ConstraintSeverity.ERROR if blocking else ConstraintSeverity.WARNING,
                        entity_type=EntityType.ACTOR,
                        entity_id=rights.actor_id,
                        reason_code=ReasonCode.CONTRACT_RESTRICTION if blocking else ReasonCode.APPROVAL_REQUIRED,
                        message=f"Performer {rights.actor_id} ({character_id}): {restriction.description}",
                        remediation="Use a different scene."
                        if blocking
                        else "Obtain performer approval before release.",
                        clip_id=clip.clip_id,
                        details={"restriction_code": restriction.code, "scene_id": clip.scene_id},
                    )
                )
        return violations


def _window_violations(
    rule_id: str,
    window: RightsWindow,
    entity_type: EntityType,
    entity_id: str,
    label: str,
    context: ConstraintContext,
    clip_id: str | None,
    remediation: str,
) -> list[ConstraintViolation]:
    """Audience, territory and date checks shared by music and performer rights."""

    def violation(reason: ReasonCode, message: str, details: dict[str, str]) -> ConstraintViolation:
        return ConstraintViolation(
            rule_id=rule_id,
            severity=ConstraintSeverity.ERROR,
            entity_type=entity_type,
            entity_id=entity_id,
            reason_code=reason,
            message=message,
            remediation=remediation,
            clip_id=clip_id,
            details=details,
        )

    violations = []
    if not window.allows_audience(context.audience):
        violations.append(
            violation(
                ReasonCode.AUDIENCE_NOT_LICENSED,
                f"{label} is not licensed for the {context.audience} audience.",
                {"audience": context.audience, "allowed_audiences": ", ".join(window.allowed_audiences) or "none"},
            )
        )
    if not window.covers_territory(context.territory):
        violations.append(
            violation(
                ReasonCode.TERRITORY_NOT_LICENSED,
                f"{label} is not licensed in territory {context.territory}.",
                {"territory": context.territory, "territories": ", ".join(window.territories)},
            )
        )
    day = context.evaluation_date
    dates = {
        "evaluation_date": day.isoformat(),
        "valid_from": window.valid_from.isoformat(),
        "valid_until": window.valid_until.isoformat(),
    }
    if day < window.valid_from:
        violations.append(
            violation(
                ReasonCode.RIGHTS_NOT_YET_ACTIVE,
                f"{label} is not licensed for promotional use until {window.valid_from} (evaluated for {day}).",
                dates,
            )
        )
    elif day > window.valid_until:
        violations.append(
            violation(
                ReasonCode.PROMOTIONAL_RIGHTS_EXPIRED,
                f"{label} promotional rights expired on {window.valid_until} (evaluated for {day}).",
                dates,
            )
        )
    return violations
