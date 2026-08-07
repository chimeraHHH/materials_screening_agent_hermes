"""Low-cost, local extraction of auditable text fragments.

The extractors deliberately return untrusted text drafts rather than commands,
policies, or scientific claims.  They never perform network I/O and they never
decode PDF content.  A caller may supply a lazy body loader, which is invoked
only when metadata is insufficient under an explicit decision.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from html.parser import HTMLParser
from typing import Any
from xml.etree import ElementTree

from material_agent.inspiration.models import PassageLocatorKind


_TOKEN_RE = re.compile(
    r"[\u3400-\u9fff]|[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*|[^\W\d_]+",
    re.UNICODE,
)
_WHITESPACE_RE = re.compile(r"\s+")
_REFERENCE_HEADING_RE = re.compile(
    r"^(?:references?|bibliography|works?\s+cited|literature\s+cited|"
    r"acknowledg(?:e)?ments?|author\s+contributions?|funding|"
    r"conflicts?\s+of\s+interest|supplementary\s+(?:material|information))$",
    re.IGNORECASE,
)
_RELEVANT_SECTION_RE = re.compile(
    r"\b(?:abstract|result|discussion|conclusion|mechanism|finding|analysis)\b",
    re.IGNORECASE,
)
_PDF_MEDIA_TYPES = {"application/pdf", "application/x-pdf"}


class ExtractionLimitError(ValueError):
    """Input exceeds an explicit local extraction budget."""


class ExtractionTier(IntEnum):
    METADATA_API = 0
    STRUCTURED_WEB = 1
    JATS = 2
    HTML_SECTION = 3


class ExtractionDecision(StrEnum):
    SKIP_BODY_ABSTRACT_SUFFICIENT = "SKIP_BODY_ABSTRACT_SUFFICIENT"
    FETCH_BODY_METADATA_INSUFFICIENT = "FETCH_BODY_METADATA_INSUFFICIENT"
    BODY_EXTRACTED = "BODY_EXTRACTED"
    SKIP_BODY_UNSUPPORTED = "SKIP_BODY_UNSUPPORTED"
    UNEXTRACTABLE = "UNEXTRACTABLE"


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    max_input_bytes: int = 1_000_000
    max_drafts: int = 64
    min_abstract_tokens: int = 80

    def __post_init__(self) -> None:
        if self.max_input_bytes < 1:
            raise ValueError("max_input_bytes must be positive")
        if not 1 <= self.max_drafts <= 2_000:
            raise ValueError("max_drafts must be between 1 and 2000")
        if not 1 <= self.min_abstract_tokens <= 512:
            raise ValueError("min_abstract_tokens must be between 1 and 512")


@dataclass(frozen=True, slots=True)
class ExtractedTextDraft:
    """A located text fragment that remains explicitly untrusted."""

    text: str
    locator_kind: PassageLocatorKind
    selector: str
    section_heading: str | None
    source_tier: ExtractionTier
    untrusted_text: bool = True

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("extracted text draft cannot be empty")
        if not self.selector.strip():
            raise ValueError("extracted text draft requires a selector")
        if self.untrusted_text is not True:
            raise ValueError("extracted page text must remain untrusted")


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    title: str | None
    keywords: tuple[str, ...]
    drafts: tuple[ExtractedTextDraft, ...]
    decision: ExtractionDecision
    media_type: str
    warnings: tuple[str, ...] = ()
    untrusted_source: bool = True

    def __post_init__(self) -> None:
        if self.untrusted_source is not True:
            raise ValueError("extracted document content must remain untrusted")


@dataclass(frozen=True, slots=True)
class FetchedBody:
    media_type: str
    payload: bytes | str


def count_approximate_tokens(text: str) -> int:
    """Return a deterministic, dependency-free token-count approximation."""

    return sum(1 for _ in _TOKEN_RE.finditer(text))


def extract_openalex_metadata(
    payload: bytes | str | Mapping[str, Any],
    *,
    target_terms: Iterable[str] = (),
    limits: ExtractionLimits | None = None,
    result_index: int = 0,
) -> ExtractionResult:
    """Extract one OpenAlex-style work and decide whether body fetch is needed."""

    selected_limits = limits or ExtractionLimits()
    if isinstance(payload, (bytes, str)) and _looks_like_pdf(payload):
        return _pdf_disabled_result("application/pdf")
    try:
        value = _load_json_value(payload, limits=selected_limits)
    except ExtractionLimitError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return ExtractionResult(
            title=None,
            keywords=(),
            drafts=(),
            decision=ExtractionDecision.UNEXTRACTABLE,
            media_type="application/json",
            warnings=("INVALID_METADATA_JSON",),
        )

    base_selector = "$"
    work: Mapping[str, Any] | None = None
    if isinstance(value, Mapping):
        results = value.get("results")
        if isinstance(results, list):
            if 0 <= result_index < len(results) and isinstance(
                results[result_index], Mapping
            ):
                work = results[result_index]
                base_selector = f"$.results[{result_index}]"
        else:
            work = value
    if work is None:
        return ExtractionResult(
            title=None,
            keywords=(),
            drafts=(),
            decision=ExtractionDecision.UNEXTRACTABLE,
            media_type="application/json",
            warnings=("METADATA_WORK_NOT_FOUND",),
        )

    title = _first_text(work.get("display_name"), work.get("title"))
    keywords = _extract_openalex_keywords(work)
    abstract = _reconstruct_inverted_abstract(work.get("abstract_inverted_index"))
    abstract_selector = f"{base_selector}.abstract_inverted_index"
    if abstract is None:
        abstract = _first_text(work.get("abstract"))
        abstract_selector = f"{base_selector}.abstract"

    drafts: tuple[ExtractedTextDraft, ...] = ()
    if abstract:
        drafts = (
            ExtractedTextDraft(
                text=abstract,
                locator_kind=PassageLocatorKind.JSON_PATH,
                selector=abstract_selector,
                section_heading="Abstract",
                source_tier=ExtractionTier.METADATA_API,
            ),
        )

    sufficient = abstract is not None and _is_sufficient_abstract(
        abstract,
        title=title,
        keywords=keywords,
        target_terms=target_terms,
        min_tokens=selected_limits.min_abstract_tokens,
    )
    return ExtractionResult(
        title=title,
        keywords=keywords,
        drafts=drafts,
        decision=(
            ExtractionDecision.SKIP_BODY_ABSTRACT_SUFFICIENT
            if sufficient
            else ExtractionDecision.FETCH_BODY_METADATA_INSUFFICIENT
        ),
        media_type="application/json",
    )


def extract_metadata_then_optional_body(
    metadata_payload: bytes | str | Mapping[str, Any],
    *,
    target_terms: Iterable[str],
    body_loader: Callable[[], FetchedBody] | None,
    limits: ExtractionLimits | None = None,
    result_index: int = 0,
) -> ExtractionResult:
    """Use metadata first and invoke ``body_loader`` only when explicitly needed."""

    selected_limits = limits or ExtractionLimits()
    terms = tuple(target_terms)
    metadata = extract_openalex_metadata(
        metadata_payload,
        target_terms=terms,
        limits=selected_limits,
        result_index=result_index,
    )
    if metadata.decision is not ExtractionDecision.FETCH_BODY_METADATA_INSUFFICIENT:
        return metadata
    if body_loader is None:
        return metadata

    fetched = body_loader()
    body = extract_document(
        fetched.payload,
        media_type=fetched.media_type,
        target_terms=terms,
        limits=selected_limits,
    )
    combined_drafts = _deduplicate_drafts(
        (*metadata.drafts, *body.drafts),
        max_drafts=selected_limits.max_drafts,
    )
    return ExtractionResult(
        title=metadata.title or body.title,
        keywords=_unique_texts((*metadata.keywords, *body.keywords)),
        drafts=combined_drafts,
        decision=body.decision,
        media_type=body.media_type,
        warnings=body.warnings,
    )


def extract_document(
    payload: bytes | str,
    *,
    media_type: str,
    target_terms: Iterable[str] = (),
    limits: ExtractionLimits | None = None,
) -> ExtractionResult:
    """Dispatch a fetched non-PDF document to a bounded local extractor."""

    selected_limits = limits or ExtractionLimits()
    normalized_media_type = media_type.partition(";")[0].strip().casefold()
    if normalized_media_type in _PDF_MEDIA_TYPES or _looks_like_pdf(payload):
        return _pdf_disabled_result(normalized_media_type or "application/pdf")
    if normalized_media_type in {"text/html", "application/xhtml+xml"}:
        return extract_html_document(
            payload,
            target_terms=target_terms,
            limits=selected_limits,
        )
    if normalized_media_type in {
        "application/xml",
        "text/xml",
        "application/jats+xml",
    }:
        return extract_jats_document(
            payload,
            target_terms=target_terms,
            limits=selected_limits,
        )
    if normalized_media_type in {"application/json", "application/vnd.api+json"}:
        return extract_openalex_metadata(
            payload,
            target_terms=target_terms,
            limits=selected_limits,
        )
    _enforce_size(payload, selected_limits)
    return ExtractionResult(
        title=None,
        keywords=(),
        drafts=(),
        decision=ExtractionDecision.UNEXTRACTABLE,
        media_type=normalized_media_type or "application/octet-stream",
        warnings=("UNSUPPORTED_CONTENT_TYPE",),
    )


def extract_html_document(
    payload: bytes | str,
    *,
    target_terms: Iterable[str] = (),
    limits: ExtractionLimits | None = None,
) -> ExtractionResult:
    """Extract JSON-LD/meta abstracts, then relevant ordinary HTML sections."""

    selected_limits = limits or ExtractionLimits()
    if _looks_like_pdf(payload):
        return _pdf_disabled_result("application/pdf")
    try:
        html_text = _decode_text(payload, limits=selected_limits)
    except UnicodeDecodeError:
        return _unextractable("text/html", "HTML_DECODE_FAILED")

    parser = _ArticleHTMLParser()
    try:
        parser.feed(html_text)
        parser.close()
    except (ValueError, TypeError):
        return _unextractable("text/html", "HTML_PARSE_FAILED")

    terms = tuple(target_terms)
    title_candidates: list[str] = []
    keywords: list[str] = []
    structured_drafts: list[ExtractedTextDraft] = []

    for script_index, (selector, script_text) in enumerate(parser.jsonld_scripts, 1):
        try:
            jsonld = json.loads(script_text)
        except (json.JSONDecodeError, TypeError):
            continue
        for path, article in _walk_article_jsonld(jsonld):
            title = _first_text(article.get("headline"), article.get("name"))
            if title:
                title_candidates.append(title)
            keywords.extend(_split_keywords(article.get("keywords")))
            abstract = _first_text(article.get("abstract"), article.get("description"))
            if abstract:
                field = "abstract" if _first_text(article.get("abstract")) else "description"
                structured_drafts.append(
                    ExtractedTextDraft(
                        text=abstract,
                        locator_kind=PassageLocatorKind.JSON_LD,
                        selector=f"{selector} {path}.{field} [script={script_index}]",
                        section_heading="Abstract",
                        source_tier=ExtractionTier.STRUCTURED_WEB,
                    )
                )

    for name, content, selector in parser.metadata:
        if name in _TITLE_META_NAMES:
            title_candidates.append(content)
        if name in _KEYWORD_META_NAMES:
            keywords.extend(_split_keywords(content))
        if name in _ABSTRACT_META_NAMES:
            structured_drafts.append(
                ExtractedTextDraft(
                    text=content,
                    locator_kind=PassageLocatorKind.HTML_META,
                    selector=selector,
                    section_heading="Abstract",
                    source_tier=ExtractionTier.STRUCTURED_WEB,
                )
            )

    if parser.document_title:
        title_candidates.append(parser.document_title)
    title = _first_text(*title_candidates)
    selected_keywords = _unique_texts(keywords)
    structured_drafts = list(
        _deduplicate_drafts(
            structured_drafts,
            max_drafts=selected_limits.max_drafts,
        )
    )
    structured_sufficient = any(
        _is_sufficient_abstract(
            draft.text,
            title=title,
            keywords=selected_keywords,
            target_terms=terms,
            min_tokens=selected_limits.min_abstract_tokens,
        )
        for draft in structured_drafts
    )

    body_drafts: list[ExtractedTextDraft] = []
    if not structured_sufficient:
        for block in parser.body_blocks:
            if _is_reference_heading(block.section_heading):
                continue
            if not _text_is_relevant(
                block.text,
                section_heading=block.section_heading,
                target_terms=terms,
            ):
                continue
            body_drafts.append(
                ExtractedTextDraft(
                    text=block.text,
                    locator_kind=PassageLocatorKind.CSS_SELECTOR,
                    selector=block.selector,
                    section_heading=block.section_heading,
                    source_tier=ExtractionTier.HTML_SECTION,
                )
            )

    drafts = _deduplicate_drafts(
        (*structured_drafts, *body_drafts),
        max_drafts=selected_limits.max_drafts,
    )
    if not drafts:
        return ExtractionResult(
            title=title,
            keywords=selected_keywords,
            drafts=(),
            decision=ExtractionDecision.UNEXTRACTABLE,
            media_type="text/html",
            warnings=("NO_RELEVANT_HTML_TEXT",),
        )
    return ExtractionResult(
        title=title,
        keywords=selected_keywords,
        drafts=drafts,
        decision=ExtractionDecision.BODY_EXTRACTED,
        media_type="text/html",
        warnings=(
            ("STRUCTURED_METADATA_SUFFICIENT",) if structured_sufficient else ()
        ),
    )


def extract_jats_document(
    payload: bytes | str,
    *,
    target_terms: Iterable[str] = (),
    limits: ExtractionLimits | None = None,
) -> ExtractionResult:
    """Extract JATS abstract/keywords and only target-matched body sections."""

    selected_limits = limits or ExtractionLimits()
    if _looks_like_pdf(payload):
        return _pdf_disabled_result("application/pdf")
    try:
        xml_text = _decode_text(payload, limits=selected_limits)
        root = ElementTree.fromstring(xml_text)
    except (UnicodeDecodeError, ElementTree.ParseError):
        return _unextractable("application/jats+xml", "JATS_PARSE_FAILED")

    terms = tuple(target_terms)
    title = _first_element_text(root, "article-title")
    keywords = _unique_texts(
        text
        for element in root.iter()
        if _local_name(element.tag) == "kwd"
        if (text := _element_text(element))
    )

    abstract_drafts: list[ExtractedTextDraft] = []
    abstract_index = 0
    for element in root.iter():
        if _local_name(element.tag) != "abstract":
            continue
        abstract_index += 1
        text = _element_text(element)
        if text:
            abstract_drafts.append(
                ExtractedTextDraft(
                    text=text,
                    locator_kind=PassageLocatorKind.JATS_XPATH,
                    selector=(
                        f"(//*[local-name()='abstract'])[{abstract_index}]"
                    ),
                    section_heading="Abstract",
                    source_tier=ExtractionTier.JATS,
                )
            )

    abstract_sufficient = any(
        _is_sufficient_abstract(
            draft.text,
            title=title,
            keywords=keywords,
            target_terms=terms,
            min_tokens=selected_limits.min_abstract_tokens,
        )
        for draft in abstract_drafts
    )

    section_drafts: list[ExtractedTextDraft] = []
    if not abstract_sufficient:
        section_index = 0
        bodies = tuple(
            element for element in root.iter() if _local_name(element.tag) == "body"
        )
        for body in bodies:
            for section in body.iter():
                if _local_name(section.tag) != "sec":
                    continue
                section_index += 1
                heading = next(
                    (
                        _element_text(child)
                        for child in list(section)
                        if _local_name(child.tag) == "title"
                    ),
                    None,
                )
                if _is_reference_heading(heading):
                    continue
                paragraph_index = 0
                for child in list(section):
                    if _local_name(child.tag) != "p":
                        continue
                    paragraph_index += 1
                    text = _element_text(child)
                    if not text or not _text_is_relevant(
                        text,
                        section_heading=heading,
                        target_terms=terms,
                    ):
                        continue
                    section_drafts.append(
                        ExtractedTextDraft(
                            text=text,
                            locator_kind=PassageLocatorKind.JATS_XPATH,
                            selector=(
                                "(//*[local-name()='body']"
                                "//*[local-name()='sec'])"
                                f"[{section_index}]/*[local-name()='p']"
                                f"[{paragraph_index}]"
                            ),
                            section_heading=heading,
                            source_tier=ExtractionTier.JATS,
                        )
                    )

    drafts = _deduplicate_drafts(
        (*abstract_drafts, *section_drafts),
        max_drafts=selected_limits.max_drafts,
    )
    if not drafts:
        return ExtractionResult(
            title=title,
            keywords=keywords,
            drafts=(),
            decision=ExtractionDecision.UNEXTRACTABLE,
            media_type="application/jats+xml",
            warnings=("NO_RELEVANT_JATS_TEXT",),
        )
    return ExtractionResult(
        title=title,
        keywords=keywords,
        drafts=drafts,
        decision=ExtractionDecision.BODY_EXTRACTED,
        media_type="application/jats+xml",
        warnings=(("JATS_ABSTRACT_SUFFICIENT",) if abstract_sufficient else ()),
    )


@dataclass(slots=True)
class _HTMLFrame:
    tag: str
    selector: str
    child_counts: dict[str, int]
    skip: bool
    capture_kind: str | None
    text_parts: list[str]
    jsonld: bool = False


@dataclass(frozen=True, slots=True)
class _HTMLBlock:
    text: str
    selector: str
    section_heading: str | None


class _ArticleHTMLParser(HTMLParser):
    _SKIP_TAGS = {
        "script",
        "style",
        "nav",
        "footer",
        "aside",
        "form",
        "noscript",
        "svg",
        "canvas",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.frames: list[_HTMLFrame] = []
        self.root_counts: dict[str, int] = {}
        self.metadata: list[tuple[str, str, str]] = []
        self.jsonld_scripts: list[tuple[str, str]] = []
        self.body_blocks: list[_HTMLBlock] = []
        self.document_title: str | None = None
        self.current_heading: str | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.casefold()
        attributes = {
            key.casefold(): value or "" for key, value in attrs if key is not None
        }
        counts = self.frames[-1].child_counts if self.frames else self.root_counts
        counts[normalized_tag] = counts.get(normalized_tag, 0) + 1
        component = f"{normalized_tag}:nth-of-type({counts[normalized_tag]})"
        selector = (
            f"{self.frames[-1].selector} > {component}"
            if self.frames
            else component
        )

        if normalized_tag == "meta":
            name = (attributes.get("name") or attributes.get("property") or "").casefold()
            content = _clean_extracted_text(attributes.get("content", ""))
            if name and content:
                self.metadata.append((name, content, selector))

        is_jsonld = normalized_tag == "script" and (
            attributes.get("type", "").partition(";")[0].strip().casefold()
            == "application/ld+json"
        )
        parent_skip = self.frames[-1].skip if self.frames else False
        marker = " ".join(
            (
                attributes.get("id", ""),
                attributes.get("class", ""),
                attributes.get("role", ""),
                attributes.get("aria-label", ""),
            )
        )
        skip = parent_skip or (
            normalized_tag in self._SKIP_TAGS and not is_jsonld
        ) or _is_reference_marker(marker)
        capture_kind: str | None = None
        if not skip:
            if normalized_tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                capture_kind = "heading"
            elif normalized_tag == "p":
                capture_kind = "paragraph"
            elif normalized_tag == "title":
                capture_kind = "title"
        self.frames.append(
            _HTMLFrame(
                tag=normalized_tag,
                selector=selector,
                child_counts={},
                skip=skip,
                capture_kind=capture_kind,
                text_parts=[],
                jsonld=is_jsonld,
            )
        )

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        for frame in reversed(self.frames):
            if frame.jsonld:
                frame.text_parts.append(data)
                return
        for frame in reversed(self.frames):
            if frame.capture_kind is not None:
                frame.text_parts.append(data)
                return

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        matching_index = next(
            (
                index
                for index in range(len(self.frames) - 1, -1, -1)
                if self.frames[index].tag == normalized_tag
            ),
            None,
        )
        if matching_index is None:
            return
        for frame in reversed(self.frames[matching_index:]):
            self._finalize(frame)
        del self.frames[matching_index:]

    def close(self) -> None:
        super().close()
        for frame in reversed(self.frames):
            self._finalize(frame)
        self.frames.clear()

    def _finalize(self, frame: _HTMLFrame) -> None:
        text = _clean_extracted_text(" ".join(frame.text_parts))
        if frame.jsonld and text:
            self.jsonld_scripts.append((frame.selector, text))
        elif frame.capture_kind == "heading" and text:
            self.current_heading = text
        elif frame.capture_kind == "title" and text and self.document_title is None:
            self.document_title = text
        elif frame.capture_kind == "paragraph" and text and not frame.skip:
            self.body_blocks.append(
                _HTMLBlock(
                    text=text,
                    selector=frame.selector,
                    section_heading=self.current_heading,
                )
            )


_TITLE_META_NAMES = {
    "citation_title",
    "dc.title",
    "dcterms.title",
    "og:title",
    "twitter:title",
}
_ABSTRACT_META_NAMES = {
    "citation_abstract",
    "dc.description",
    "dcterms.abstract",
    "dcterms.description",
    "og:description",
    "description",
}
_KEYWORD_META_NAMES = {
    "citation_keywords",
    "dc.subject",
    "dcterms.subject",
    "keywords",
    "article:tag",
}


def _load_json_value(
    payload: bytes | str | Mapping[str, Any],
    *,
    limits: ExtractionLimits,
) -> Any:
    if isinstance(payload, Mapping):
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _enforce_size(encoded, limits)
        return payload
    return json.loads(_decode_text(payload, limits=limits))


def _decode_text(payload: bytes | str, *, limits: ExtractionLimits) -> str:
    _enforce_size(payload, limits)
    if isinstance(payload, bytes):
        return payload.decode("utf-8")
    return payload


def _enforce_size(payload: bytes | str, limits: ExtractionLimits) -> None:
    size = len(payload) if isinstance(payload, bytes) else len(payload.encode("utf-8"))
    if size > limits.max_input_bytes:
        raise ExtractionLimitError(
            f"input size {size} exceeds max_input_bytes={limits.max_input_bytes}"
        )


def _looks_like_pdf(payload: bytes | str) -> bool:
    prefix = payload[:8] if isinstance(payload, bytes) else payload[:8].encode("utf-8")
    return prefix.lstrip().startswith(b"%PDF-")


def _reconstruct_inverted_abstract(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    positioned_words: dict[int, str] = {}
    for word, positions in value.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int) and not isinstance(position, bool) and position >= 0:
                existing = positioned_words.get(position)
                if existing is not None and existing != word:
                    return None
                positioned_words[position] = word
    if not positioned_words:
        return None
    return _clean_extracted_text(
        " ".join(word for _, word in sorted(positioned_words.items()))
    ) or None


def _extract_openalex_keywords(work: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for field in ("keywords", "concepts", "topics"):
        items = work.get(field)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, Mapping):
                text = _first_text(
                    item.get("display_name"),
                    item.get("name"),
                    item.get("keyword"),
                )
                if text:
                    values.append(text)
    return _unique_texts(values)


def _walk_article_jsonld(value: Any, path: str = "$") -> Iterable[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        raw_type = value.get("@type")
        if isinstance(raw_type, str):
            types = {raw_type}
        elif isinstance(raw_type, list):
            types = {item for item in raw_type if isinstance(item, str)}
        else:
            types = set()
        if types & {"ScholarlyArticle", "Article", "NewsArticle", "TechArticle"}:
            yield path, value
        for key, child in value.items():
            if isinstance(child, (Mapping, list)):
                yield from _walk_article_jsonld(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_article_jsonld(child, f"{path}[{index}]")


def _split_keywords(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return _unique_texts(re.split(r"[,;|]", value))
    if isinstance(value, list):
        return _unique_texts(item for item in value if isinstance(item, str))
    return ()


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            cleaned = _clean_extracted_text(value)
            if cleaned:
                return cleaned
    return None


def _unique_texts(values: Iterable[str]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_extracted_text(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned)
    return tuple(output)


def _clean_extracted_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


def _normalized_terms(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                cleaned.casefold()
                for value in values
                if (cleaned := _clean_extracted_text(value))
            }
        )
    )


def _is_sufficient_abstract(
    abstract: str,
    *,
    title: str | None,
    keywords: tuple[str, ...],
    target_terms: Iterable[str],
    min_tokens: int,
) -> bool:
    if count_approximate_tokens(abstract) < min_tokens:
        return False
    terms = _normalized_terms(target_terms)
    if not terms:
        return True
    haystack = " ".join((title or "", *keywords, abstract)).casefold()
    return any(term in haystack for term in terms)


def _text_is_relevant(
    text: str,
    *,
    section_heading: str | None,
    target_terms: Iterable[str],
) -> bool:
    terms = _normalized_terms(target_terms)
    haystack = f"{section_heading or ''} {text}".casefold()
    if terms:
        return any(term in haystack for term in terms)
    return bool(section_heading and _RELEVANT_SECTION_RE.search(section_heading))


def _is_reference_heading(value: str | None) -> bool:
    if not value:
        return False
    cleaned = _clean_extracted_text(value).strip(" .:#-_/")
    return bool(_REFERENCE_HEADING_RE.fullmatch(cleaned))


def _is_reference_marker(value: str) -> bool:
    marker = _clean_extracted_text(value).casefold()
    if _is_reference_heading(marker):
        return True
    tokens = set(re.findall(r"[a-z0-9]+", marker))
    return bool(
        {"references", "bibliography"} & tokens
        or "ref-list" in marker
        or "reference-list" in marker
    )


def _deduplicate_drafts(
    drafts: Iterable[ExtractedTextDraft],
    *,
    max_drafts: int,
) -> tuple[ExtractedTextDraft, ...]:
    output: list[ExtractedTextDraft] = []
    seen: set[str] = set()
    for draft in drafts:
        key = _clean_extracted_text(draft.text).casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(draft)
        if len(output) >= max_drafts:
            break
    return tuple(output)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _element_text(element: ElementTree.Element) -> str | None:
    return _first_text(" ".join(element.itertext()))


def _first_element_text(root: ElementTree.Element, local_name: str) -> str | None:
    return next(
        (
            text
            for element in root.iter()
            if _local_name(element.tag) == local_name
            if (text := _element_text(element))
        ),
        None,
    )


def _unextractable(media_type: str, warning: str) -> ExtractionResult:
    return ExtractionResult(
        title=None,
        keywords=(),
        drafts=(),
        decision=ExtractionDecision.UNEXTRACTABLE,
        media_type=media_type,
        warnings=(warning,),
    )


def _pdf_disabled_result(media_type: str) -> ExtractionResult:
    return ExtractionResult(
        title=None,
        keywords=(),
        drafts=(),
        decision=ExtractionDecision.SKIP_BODY_UNSUPPORTED,
        media_type=media_type,
        warnings=("PDF_EXTRACTION_DISABLED",),
    )
