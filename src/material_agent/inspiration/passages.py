"""Deterministic normalization, scoring, windowing, and local passage dedup."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from material_agent.inspiration.extractors import (
    ExtractedTextDraft,
    ExtractionTier,
    count_approximate_tokens,
)
from material_agent.inspiration.models import (
    ComponentSnapshotV1,
    PassageLocatorV1,
    canonical_sha256,
)
from material_agent.inspiration.policy import PassageBudgetV1


_TOKEN_RE = re.compile(
    r"[\u3400-\u9fff]|[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*|[^\W\d_]+",
    re.UNICODE,
)
_SENTENCE_RE = re.compile(r"[^.!?。！？]+(?:[.!?。！？]+|$)", re.UNICODE)
_DEFAULT_CLAIM_CUES = (
    "because",
    "demonstrate",
    "find that",
    "indicate",
    "leads to",
    "mechanism",
    "requires",
    "results show",
    "suggest",
    "suppresses",
    "enhances",
    "breaks",
)
_SECTION_CUES = ("abstract", "result", "discussion", "mechanism", "conclusion")

_PASSAGE_SELECTOR_SPEC = {
    "component": "passage-selector-normalizer-v1",
    "normalization": "html-unescape+unicode-nfkc+strip-control+collapse-whitespace",
    "token_pattern": _TOKEN_RE.pattern,
    "sentence_pattern": _SENTENCE_RE.pattern,
    "window": "term-centered-sentence-expansion-then-token-slice",
    "score": {
        "lexical": 0.12,
        "tag": 0.18,
        "claim_cue": 0.06,
        "section": 0.08,
        "tier": 0.02,
    },
    "dedup": "normalized-sha256-then-token-set-jaccard",
    "ranking": "score+coverage+cues+tier+locator+sha256",
}
PASSAGE_SELECTOR_SNAPSHOT = ComponentSnapshotV1(
    component_id="passage-selector-normalizer",
    version="1",
    implementation_sha256=canonical_sha256(_PASSAGE_SELECTOR_SPEC),
)


@dataclass(frozen=True, slots=True)
class PassageSelectionConfig:
    min_tokens: int = 80
    max_tokens: int = 220
    max_per_hit: int = 3
    local_dedup_jaccard: float = 0.85
    claim_cues: tuple[str, ...] = _DEFAULT_CLAIM_CUES

    def __post_init__(self) -> None:
        if not 1 <= self.min_tokens <= 512:
            raise ValueError("min_tokens must be between 1 and 512")
        if not self.min_tokens <= self.max_tokens <= 512:
            raise ValueError("max_tokens must be between min_tokens and 512")
        if not 1 <= self.max_per_hit <= 10:
            raise ValueError("max_per_hit must be between 1 and 10")
        if not 0.0 <= self.local_dedup_jaccard <= 1.0:
            raise ValueError("local_dedup_jaccard must be between zero and one")
        normalized_cues = tuple(normalize_text(value).casefold() for value in self.claim_cues)
        if not all(normalized_cues) or len(set(normalized_cues)) != len(normalized_cues):
            raise ValueError("claim cues must be non-empty and unique after normalization")


@dataclass(frozen=True, slots=True)
class SelectedPassageDraft:
    """Located, normalized data ready to populate a ``PassageV1``."""

    hit_id: str
    document_id: str
    locator: PassageLocatorV1
    text: str
    estimated_token_count: int
    normalized_text_sha256: str
    matched_tag_ids: tuple[str, ...]
    lexical_score: float
    source_tier: ExtractionTier
    normalizer: ComponentSnapshotV1 = PASSAGE_SELECTOR_SNAPSHOT
    untrusted_text: bool = True

    def __post_init__(self) -> None:
        if self.normalizer != PASSAGE_SELECTOR_SNAPSHOT:
            raise ValueError("selected passage requires the frozen selector snapshot")
        if self.untrusted_text is not True:
            raise ValueError("selected page text must remain untrusted")


def selection_config_from_policy(policy: PassageBudgetV1) -> PassageSelectionConfig:
    """Map the frozen run policy to the pure passage selector configuration."""

    return PassageSelectionConfig(
        min_tokens=policy.min_tokens,
        max_tokens=policy.max_tokens,
        max_per_hit=policy.max_per_hit,
        local_dedup_jaccard=policy.local_dedup_jaccard,
        claim_cues=policy.claim_cues,
    )


@dataclass(frozen=True, slots=True)
class _ScoredWindow:
    selected: SelectedPassageDraft
    lexical_hits: int
    tag_hits: int
    cue_hits: int
    section_bonus: int
    token_fingerprint: frozenset[str]


def normalize_text(value: str) -> str:
    """Normalize Unicode/HTML whitespace without interpreting page instructions."""

    decoded = html.unescape(value)
    normalized = unicodedata.normalize("NFKC", decoded)
    characters: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if character.isspace():
            characters.append(" ")
        elif category in {"Cc", "Cf", "Cs"}:
            continue
        else:
            characters.append(character)
    return " ".join("".join(characters).split())


def select_passage_drafts(
    drafts: Iterable[ExtractedTextDraft],
    *,
    hit_id: str,
    document_id: str,
    query_terms: Iterable[str],
    tag_terms: Mapping[str, Iterable[str]],
    config: PassageSelectionConfig | None = None,
    document_title: str | None = None,
) -> tuple[SelectedPassageDraft, ...]:
    """Select bounded, relevant, locally deduplicated passages for one hit."""

    selected_config = config or PassageSelectionConfig()
    normalized_query_terms = _normalized_unique(query_terms)
    normalized_tag_terms = {
        tag_id: _normalized_unique(terms)
        for tag_id, terms in sorted(tag_terms.items())
    }
    normalized_cues = _normalized_unique(selected_config.claim_cues)
    normalized_title = normalize_text(document_title or "")

    scored: list[_ScoredWindow] = []
    for draft in drafts:
        normalized_source = normalize_text(draft.text)
        if not normalized_source:
            continue
        heading = normalize_text(draft.section_heading or "")
        ranking_context = " ".join(
            value for value in (normalized_title, heading) if value
        )
        for window_text, start_offset, end_offset in _bounded_windows(
            normalized_source,
            heading=ranking_context,
            query_terms=normalized_query_terms,
            tag_terms=normalized_tag_terms,
            config=selected_config,
        ):
            score = _score_window(
                window_text,
                heading=ranking_context,
                query_terms=normalized_query_terms,
                tag_terms=normalized_tag_terms,
                claim_cues=normalized_cues,
                source_tier=draft.source_tier,
            )
            if score is None:
                continue
            lexical_score, lexical_hits, matched_tags, cue_hits, section_bonus = score
            token_count = count_approximate_tokens(window_text)
            locator = PassageLocatorV1(
                kind=draft.locator_kind,
                selector=draft.selector,
                section_heading=heading or None,
                start_offset=start_offset,
                end_offset=end_offset,
            )
            selected = SelectedPassageDraft(
                hit_id=hit_id,
                document_id=document_id,
                locator=locator,
                text=window_text,
                estimated_token_count=token_count,
                normalized_text_sha256=hashlib.sha256(
                    window_text.encode("utf-8")
                ).hexdigest(),
                matched_tag_ids=matched_tags,
                lexical_score=lexical_score,
                source_tier=draft.source_tier,
            )
            scored.append(
                _ScoredWindow(
                    selected=selected,
                    lexical_hits=lexical_hits,
                    tag_hits=len(matched_tags),
                    cue_hits=cue_hits,
                    section_bonus=section_bonus,
                    token_fingerprint=frozenset(_casefolded_tokens(window_text)),
                )
            )

    scored.sort(key=_ranking_key)
    output: list[SelectedPassageDraft] = []
    fingerprints: list[frozenset[str]] = []
    hashes: set[str] = set()
    for candidate in scored:
        digest = candidate.selected.normalized_text_sha256
        if digest in hashes:
            continue
        if any(
            _jaccard(candidate.token_fingerprint, previous)
            >= selected_config.local_dedup_jaccard
            for previous in fingerprints
        ):
            continue
        output.append(candidate.selected)
        hashes.add(digest)
        fingerprints.append(candidate.token_fingerprint)
        if len(output) >= selected_config.max_per_hit:
            break
    return tuple(output)


def _bounded_windows(
    text: str,
    *,
    heading: str,
    query_terms: tuple[str, ...],
    tag_terms: Mapping[str, tuple[str, ...]],
    config: PassageSelectionConfig,
) -> tuple[tuple[str, int, int], ...]:
    token_count = count_approximate_tokens(text)
    if token_count < config.min_tokens:
        return ()
    if token_count <= config.max_tokens:
        return ((text, 0, len(text)),)

    all_terms = tuple(
        sorted(
            {
                *query_terms,
                *(term for terms in tag_terms.values() for term in terms),
            }
        )
    )
    sentence_spans = [
        (match.start(), match.end())
        for match in _SENTENCE_RE.finditer(text)
        if match.group().strip()
    ]
    if not sentence_spans:
        return (_token_slice(text, config.max_tokens, all_terms),)

    relevant_indices = [
        index
        for index, (start, end) in enumerate(sentence_spans)
        if _contains_any(text[start:end], all_terms)
    ]
    if not relevant_indices and _contains_any(heading, all_terms):
        relevant_indices = [0]
    if not relevant_indices:
        return ()

    windows: list[tuple[str, int, int]] = []
    for center_index in relevant_indices:
        start, end = sentence_spans[center_index]
        if count_approximate_tokens(text[start:end]) > config.max_tokens:
            windows.append(
                _token_slice(
                    text[start:end],
                    config.max_tokens,
                    all_terms,
                    base_offset=start,
                )
            )
            continue

        left = center_index - 1
        right = center_index + 1
        prefer_left = True
        while True:
            options: list[tuple[str, int, int]] = []
            if left >= 0:
                options.append(("left", sentence_spans[left][0], end))
            if right < len(sentence_spans):
                options.append(("right", start, sentence_spans[right][1]))
            if not options:
                break
            preferred_side = "left" if prefer_left else "right"
            options.sort(
                key=lambda item: (item[0] != preferred_side, item[0])
            )
            selected_side: str | None = None
            for side, candidate_start, candidate_end in options:
                candidate_tokens = count_approximate_tokens(
                    text[candidate_start:candidate_end]
                )
                if candidate_tokens <= config.max_tokens:
                    start, end = candidate_start, candidate_end
                    selected_side = side
                    break
            if selected_side is None:
                break
            if selected_side == "left":
                left -= 1
            else:
                right += 1
            prefer_left = not prefer_left

        window = text[start:end].strip()
        adjusted_start = text.find(window, start, end)
        adjusted_end = adjusted_start + len(window)
        if count_approximate_tokens(window) >= config.min_tokens:
            windows.append((window, adjusted_start, adjusted_end))

    if not windows:
        fallback = _token_slice(text, config.max_tokens, all_terms)
        if count_approximate_tokens(fallback[0]) >= config.min_tokens:
            windows.append(fallback)

    unique: list[tuple[str, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for window in windows:
        key = (window[1], window[2])
        if key not in seen:
            seen.add(key)
            unique.append(window)
    return tuple(unique)


def _token_slice(
    text: str,
    max_tokens: int,
    preferred_terms: tuple[str, ...],
    *,
    base_offset: int = 0,
) -> tuple[str, int, int]:
    matches = list(_TOKEN_RE.finditer(text))
    if len(matches) <= max_tokens:
        cleaned = text.strip()
        start = text.find(cleaned)
        return cleaned, base_offset + start, base_offset + start + len(cleaned)

    folded = text.casefold()
    preferred_character = next(
        (
            position
            for term in preferred_terms
            if (position := folded.find(term.casefold())) >= 0
        ),
        0,
    )
    center = next(
        (
            index
            for index, match in enumerate(matches)
            if match.start() <= preferred_character < match.end()
        ),
        0,
    )
    start_index = max(0, min(center - max_tokens // 2, len(matches) - max_tokens))
    end_index = start_index + max_tokens
    start = matches[start_index].start()
    end = matches[end_index - 1].end()
    return text[start:end], base_offset + start, base_offset + end


def _score_window(
    text: str,
    *,
    heading: str,
    query_terms: tuple[str, ...],
    tag_terms: Mapping[str, tuple[str, ...]],
    claim_cues: tuple[str, ...],
    source_tier: ExtractionTier,
) -> tuple[float, int, tuple[str, ...], int, int] | None:
    haystack = f"{heading} {text}".casefold()
    lexical_hits = sum(1 for term in query_terms if term in haystack)
    matched_tags = tuple(
        tag_id
        for tag_id, terms in sorted(tag_terms.items())
        if any(term in haystack for term in terms)
    )
    if lexical_hits == 0 and not matched_tags:
        return None
    cue_hits = sum(1 for cue in claim_cues if cue in text.casefold())
    section_bonus = int(any(cue in heading.casefold() for cue in _SECTION_CUES))
    tier_bonus = max(0, 3 - int(source_tier))
    raw_score = (
        0.12 * min(lexical_hits, 4)
        + 0.18 * min(len(matched_tags), 3)
        + 0.06 * min(cue_hits, 3)
        + 0.08 * section_bonus
        + 0.02 * tier_bonus
    )
    return (
        round(min(1.0, raw_score), 12),
        lexical_hits,
        matched_tags,
        cue_hits,
        section_bonus,
    )


def _normalized_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                normalized.casefold()
                for value in values
                if (normalized := normalize_text(value))
            }
        )
    )


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    folded = text.casefold()
    return any(term in folded for term in terms)


def _casefolded_tokens(text: str) -> tuple[str, ...]:
    return tuple(match.group().casefold() for match in _TOKEN_RE.finditer(text))


def _jaccard(first: frozenset[str], second: frozenset[str]) -> float:
    if not first and not second:
        return 1.0
    return len(first & second) / len(first | second)


def _ranking_key(candidate: _ScoredWindow) -> tuple[object, ...]:
    selected = candidate.selected
    return (
        -selected.lexical_score,
        -candidate.tag_hits,
        -candidate.lexical_hits,
        -candidate.cue_hits,
        -candidate.section_bonus,
        int(selected.source_tier),
        selected.locator.selector,
        selected.locator.start_offset,
        selected.normalized_text_sha256,
    )
