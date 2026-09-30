"""Referential checks for rights, policies, audiences, performance and budget."""

from trailer_director.data.parsing import ParsedDataset
from trailer_director.data.report import ValidationReport
from trailer_director.domain import AudienceProfile, AudienceType, CostCategory
from trailer_director.domain.language import stereotyping_phrases


def check_commercial(parsed: ParsedDataset, report: ValidationReport) -> None:
    _check_actor_rights(parsed, report)
    _check_rating_policies(parsed, report)
    _check_audience_profiles(parsed, report)
    _check_historical_performance(parsed, report)
    _check_cost_sheet(parsed, report)


def _check_actor_rights(parsed: ParsedDataset, report: ValidationReport) -> None:
    covered: dict[str, str] = {}
    for rights in parsed.actor_rights.values():
        location = f"actor_rights.{rights.actor_id}"
        character_id = rights.character_id
        if parsed.resolves("characters", character_id, f"{location}.character_id", report):
            if character_id in covered:
                report.error(
                    f"{location}.character_id", f"'{character_id}' is already covered by {covered[character_id]}"
                )
            else:
                covered[character_id] = rights.actor_id

        for index, restriction in enumerate(rights.restrictions):
            for scene_index, scene_id in enumerate(restriction.scene_ids):
                scene_location = f"{location}.restrictions[{index}].scene_ids[{scene_index}]"
                parsed.resolves("scenes", scene_id, scene_location, report)

    speakers = {line.speaker_id for line in parsed.dialogue.values()}
    for character_id in sorted(speakers & parsed.characters.keys() - covered.keys()):
        report.warning(f"characters.{character_id}", "speaks on screen but has no actor rights record")


def _check_rating_policies(parsed: ParsedDataset, report: ValidationReport) -> None:
    for audience in AudienceType:
        if audience not in parsed.rating_policies:
            report.error("rating_policies", f"no policy for audience '{audience}'")


def _check_audience_profiles(parsed: ParsedDataset, report: ValidationReport) -> None:
    known_campaigns = {record.campaign_id for record in parsed.historical_performance}

    for audience in AudienceType:
        if audience not in parsed.audience_profiles:
            report.error("audience_profiles", f"no profile for audience '{audience}'")

    for profile in parsed.audience_profiles.values():
        location = f"audience_profiles.{profile.audience}"
        for index, evidence in enumerate(profile.evidence):
            for campaign_index, campaign_id in enumerate(evidence.campaign_ids):
                if campaign_id not in known_campaigns:
                    report.error(
                        f"{location}.evidence[{index}].campaign_ids[{campaign_index}]",
                        f"unknown campaign id '{campaign_id}'",
                    )

        _check_stereotyping(profile, location, report)

        policy = parsed.rating_policies.get(profile.audience)
        if policy and profile.target_trailer_duration_seconds > policy.max_trailer_duration_seconds:
            report.error(
                f"{location}.target_trailer_duration_seconds",
                f"{profile.target_trailer_duration_seconds}s exceeds the rating policy maximum of "
                f"{policy.max_trailer_duration_seconds}s",
            )


def _check_stereotyping(profile: AudienceProfile, location: str, report: ValidationReport) -> None:
    """Preferences phrased as stereotypes are not evidence; the planner never sees them (planning.strategy)."""
    fields = {
        "description": [profile.description],
        "preferred_themes": profile.preferred_themes,
        "preferred_tones": profile.preferred_tones,
        "pacing": [profile.pacing],
        "positioning_notes": [profile.positioning_notes],
    }
    for field, texts in fields.items():
        for text in texts:
            phrases = stereotyping_phrases(text)
            if phrases:
                report.warning(
                    f"{location}.{field}",
                    f"stereotyping language ({', '.join(phrases)}); withheld from planning, needs cultural review",
                )


def _check_historical_performance(parsed: ParsedDataset, report: ValidationReport) -> None:
    seen: set[tuple[str, str, str]] = set()
    for record in parsed.historical_performance:
        location = f"historical_performance[{record.campaign_id}/{record.scene_id}/{record.audience}]"
        parsed.resolves("scenes", record.scene_id, f"{location}.scene_id", report)
        if record.key in seen:
            report.error(location, "duplicate record for this campaign, scene and audience")
        seen.add(record.key)


def _check_cost_sheet(parsed: ParsedDataset, report: ValidationReport) -> None:
    sheet = parsed.cost_sheet
    if sheet is None:
        return
    priciest_call = max(item.unit_cost for item in sheet.costs_in(CostCategory.MODEL_CALL))
    worst_case = priciest_call * sheet.limits.max_model_calls
    if worst_case > sheet.limits.max_estimated_total_cost:
        report.warning(
            "cost_sheet.limits",
            f"max_model_calls at the highest model rate costs {worst_case:.2f} {sheet.currency}, "
            f"above max_estimated_total_cost {sheet.limits.max_estimated_total_cost:.2f}; "
            "the cost limit will bind before the call limit",
        )
