"""Accessibility and dialect review, from subtitle-safety flags, line timing and policy requirements.

All are review items (WARNING), not blocks: the line can be used once a
person has checked the subtitle or the regional term.
"""

from trailer_director.constraints.models import (
    ConstraintContext,
    ConstraintSeverity,
    ConstraintViolation,
    EntityType,
    ReasonCode,
)
from trailer_director.constraints.rules.base import lines_in_clip
from trailer_director.domain import EpisodePackage
from trailer_director.domain.edit import TrailerClip
from trailer_director.domain.language import address_terms, terms_in_text


class SubtitleSafetyRule:
    """Where the policy requires subtitles, lines marked not subtitle-safe need subtitle review."""

    rule_id = "ACCESSIBILITY_SUBTITLE_001"

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        if not evidence.rating_policy(context.audience).subtitles_required:
            return []
        return [
            ConstraintViolation(
                rule_id=self.rule_id,
                severity=ConstraintSeverity.WARNING,
                entity_type=EntityType.DIALOGUE,
                entity_id=line.dialogue_id,
                reason_code=ReasonCode.SUBTITLE_REVIEW_REQUIRED,
                message=f"Dialogue {line.dialogue_id} is not subtitle-safe"
                + (f": {line.notes}" if line.notes else "."),
                remediation="Have the subtitle reviewed before release, or use a subtitle-safe line.",
                clip_id=clip.clip_id,
            )
            for line in lines_in_clip(clip, evidence)
            if not line.subtitle_safe
        ]


MAX_SUBTITLE_CHARS_PER_SECOND = 17.0
"""Review threshold, not a policy value: the dataset's policies set none. 17 characters per second is a
common subtitling guideline for general and children's audiences; faster text is hard to read in time."""


class SubtitleReadingSpeedRule:
    """Where the policy requires subtitles, lines whose text would read too fast for their spoken duration.

    Uses the line's own start/end as the subtitle's display time - the dataset has no separate subtitle
    events - so this is a proxy that tells a person where to re-time or condense, not a final measurement.
    """

    rule_id = "ACCESSIBILITY_READING_SPEED_001"

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        if not evidence.rating_policy(context.audience).subtitles_required:
            return []
        violations = []
        for line in lines_in_clip(clip, evidence):
            speed = len(line.text) / ((line.end - line.start) / 1000)
            if speed > MAX_SUBTITLE_CHARS_PER_SECOND:
                violations.append(
                    ConstraintViolation(
                        rule_id=self.rule_id,
                        severity=ConstraintSeverity.WARNING,
                        entity_type=EntityType.DIALOGUE,
                        entity_id=line.dialogue_id,
                        reason_code=ReasonCode.SUBTITLE_READING_SPEED_HIGH,
                        message=f"Dialogue {line.dialogue_id} needs {speed:.1f} characters per second as a "
                        f"subtitle (review above {MAX_SUBTITLE_CHARS_PER_SECOND:g}).",
                        remediation="Have the subtitle condensed or re-timed, or use a slower line.",
                        clip_id=clip.clip_id,
                        details={"chars_per_second": f"{speed:.1f}"},
                    )
                )
        return violations


class DialectReviewRule:
    """Where the policy requires dialect review, lines using regional address terms need a regional reviewer."""

    rule_id = "DIALECT_REVIEW_001"

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        if not evidence.rating_policy(context.audience).dialect_review_required:
            return []
        terms = address_terms(evidence.characters.values())
        violations = []
        for line in lines_in_clip(clip, evidence):
            used = terms_in_text(line.text, terms)
            if used:
                violations.append(
                    ConstraintViolation(
                        rule_id=self.rule_id,
                        severity=ConstraintSeverity.WARNING,
                        entity_type=EntityType.DIALOGUE,
                        entity_id=line.dialogue_id,
                        reason_code=ReasonCode.DIALECT_REVIEW_REQUIRED,
                        message=f"Dialogue {line.dialogue_id} uses the regional address term(s) {', '.join(used)}.",
                        remediation="Confirm the term's meaning and subtitle with a regional language reviewer.",
                        clip_id=clip.clip_id,
                        details={"terms": ", ".join(used), "addresses": ", ".join(terms[t] for t in used)},
                    )
                )
        return violations
