"""Story-truth check for the hook line.

Every hook sentence must appear in a dialogue line the audience may use: a
hook quoting a spoiler line leaks it even if no clip shows it. A usable line
that is not in the cut is only a warning. Title and positioning are free
marketing copy and are not checked here.
"""

import re

from trailer_director.domain import EpisodePackage, ValidationSeverity
from trailer_director.planning.models import IssueCode, PlanningIssue, PlanProposal
from trailer_director.planning.pool import EvidencePool

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "—": "-", "–": "-"})
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_EDGE = " .!?\"'"


def check_hook(proposal: PlanProposal, evidence: EpisodePackage, pool: EvidencePool) -> list[PlanningIssue]:
    in_cut = {d for clip in proposal.candidate.clips for d in clip.dialogue_ids}
    lines = {line.dialogue_id: _normal(line.text) for line in evidence.dialogue.values()}
    issues = []
    for sentence in _sentences(proposal.hook):
        sources = sorted(d for d, text in lines.items() if sentence in text)
        if not sources:
            issues.append(
                _issue(ValidationSeverity.ERROR, IssueCode.HOOK_NOT_IN_EVIDENCE, sentence, "no dialogue line")
            )
            continue
        usable = [d for d in sources if pool.is_eligible(d)]
        if not usable:
            issues.append(
                _issue(
                    ValidationSeverity.ERROR,
                    IssueCode.HOOK_USES_INELIGIBLE_LINE,
                    sentence,
                    f"only {', '.join(sources)}, which this audience may not use",
                )
            )
        elif not set(usable) & in_cut:
            issues.append(
                _issue(
                    ValidationSeverity.WARNING,
                    IssueCode.HOOK_NOT_IN_TRAILER,
                    sentence,
                    f"{', '.join(usable)}, which is not in the cut",
                )
            )
    return issues


def quote_status(text: str, evidence: EpisodePackage, pool: EvidencePool) -> tuple[list[str], list[str]]:
    """Sentences of ``text`` that quote no dialogue line, and those that quote only lines the audience may not use."""
    lines = {line.dialogue_id: _normal(line.text) for line in evidence.dialogue.values()}
    unsupported, ineligible = [], []
    for sentence in _sentences(text):
        sources = [d for d, line in lines.items() if sentence in line]
        if not sources:
            unsupported.append(sentence)
        elif not any(pool.is_eligible(d) for d in sources):
            ineligible.append(sentence)
    return unsupported, ineligible


def hook_sources(hook: str, evidence: EpisodePackage) -> list[str]:
    """The dialogue lines the hook quotes, in evidence order."""
    sentences = _sentences(hook)
    return sorted(
        line.dialogue_id
        for line in evidence.dialogue.values()
        if any(sentence in _normal(line.text) for sentence in sentences)
    )


def _sentences(hook: str) -> list[str]:
    return [s.strip(_EDGE) for s in _SENTENCE_END.split(_normal(hook)) if s.strip(_EDGE)]


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_QUOTES).casefold()).strip()


def _issue(severity: ValidationSeverity, code: IssueCode, sentence: str, found: str) -> PlanningIssue:
    return PlanningIssue(
        severity=severity, code=code, location="hook", message=f'hook sentence "{sentence}" is found in {found}'
    )
