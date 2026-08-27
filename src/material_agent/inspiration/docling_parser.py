"""Optional local-only Docling fallback for OA PDF structure extraction."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Protocol

from material_agent.inspiration.models import canonical_json_bytes
from material_agent.inspiration.research_graph import FineGrainedEvidenceSpanV1

DOCLING_LOCAL_BUNDLE_ENV = "DOCLING_LOCAL_BUNDLE"


class DoclingConverter(Protocol):
    def convert(
        self,
        source: object,
        *,
        raises_on_error: bool,
        max_num_pages: int,
        max_file_size: int,
    ) -> object: ...


class DoclingLocalParser:
    """Convert bounded in-memory PDFs without authorizing URL or model downloads."""

    def __init__(
        self,
        *,
        converter: DoclingConverter,
        stream_factory: Callable[[str, BytesIO], object],
    ) -> None:
        self.converter = converter
        self.stream_factory = stream_factory

    @classmethod
    def from_local_bundle(cls, bundle_path: Path | str) -> DoclingLocalParser:
        root = Path(bundle_path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Docling local bundle must be a directory")
        try:
            from docling.datamodel.base_models import DocumentStream, InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as exc:
            raise RuntimeError("Docling optional dependency is not installed") from exc
        options = PdfPipelineOptions(artifacts_path=root)
        options.do_ocr = False
        if hasattr(options, "do_picture_description"):
            options.do_picture_description = False
        converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
        )
        return cls(
            converter=converter,
            stream_factory=lambda name, stream: DocumentStream(
                name=name, stream=stream
            ),
        )

    def parse(
        self,
        pdf_payload: bytes,
        *,
        document_id: str,
        evidence_id: str,
        pdf_artifact_uri: str,
        structured_artifact_uri: str,
        max_spans: int = 256,
    ) -> tuple[str, bytes, tuple[FineGrainedEvidenceSpanV1, ...]]:
        if not pdf_payload.startswith(b"%PDF-") or len(pdf_payload) > 10_000_000:
            raise ValueError("Docling input must be a bounded PDF")
        source = self.stream_factory("paper.pdf", BytesIO(pdf_payload))
        result = self.converter.convert(
            source,
            raises_on_error=True,
            max_num_pages=200,
            max_file_size=10_000_000,
        )
        document = getattr(result, "document", None)
        if document is None or not hasattr(document, "export_to_markdown"):
            raise ValueError("Docling result does not expose a document")
        markdown = document.export_to_markdown()
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValueError("Docling returned empty Markdown")
        if not hasattr(document, "export_to_dict"):
            raise ValueError("Docling document does not expose structured export")
        structured = canonical_json_bytes(document.export_to_dict())
        spans = parse_docling_markdown_spans(
            markdown,
            document_id=document_id,
            evidence_id=evidence_id,
            pdf_artifact_uri=pdf_artifact_uri,
            structured_artifact_uri=structured_artifact_uri,
            max_spans=max_spans,
        )
        return markdown, structured, spans


def docling_parser_from_environment(
    environment: dict[str, str] | None = None,
) -> DoclingLocalParser | None:
    selected = dict(os.environ if environment is None else environment)
    path = selected.get(DOCLING_LOCAL_BUNDLE_ENV, "").strip()
    return DoclingLocalParser.from_local_bundle(path) if path else None


def parse_docling_markdown_spans(
    markdown: str,
    *,
    document_id: str,
    evidence_id: str,
    pdf_artifact_uri: str,
    structured_artifact_uri: str,
    max_spans: int = 256,
) -> tuple[FineGrainedEvidenceSpanV1, ...]:
    headings: list[str] = []
    paragraphs: list[tuple[tuple[str, ...], str]] = []
    buffer: list[str] = []

    def flush() -> None:
        text = " ".join(" ".join(buffer).split())
        if text:
            paragraphs.append((tuple(headings), text))
        buffer.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("#") and line.lstrip("#").startswith(" "):
            flush()
            level = len(line) - len(line.lstrip("#"))
            heading = line[level:].strip()
            headings[level - 1 :] = [heading]
        elif not line:
            flush()
        elif not line.startswith(("![", "<figure", "<table")):
            buffer.append(line)
    flush()
    spans: list[FineGrainedEvidenceSpanV1] = []
    for index, (section_path, text) in enumerate(paragraphs[:max_spans], start=1):
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        locator = (
            f"section={' > '.join(section_path) or 'body'};paragraph={index};"
            "pages=unavailable"
        )
        span_id = "span-" + hashlib.sha256(
            canonical_json_bytes(
                {
                    "evidence_id": evidence_id,
                    "locator": locator,
                    "text_sha256": text_hash,
                }
            )
        ).hexdigest()[:24]
        spans.append(
            FineGrainedEvidenceSpanV1(
                span_id=span_id,
                document_id=document_id,
                evidence_id=evidence_id,
                section_path=section_path,
                paragraph_index=index,
                text_excerpt=text[:2_000],
                text_sha256=text_hash,
                locator=locator,
                parser="DOCLING",
                structured_artifact_uri=structured_artifact_uri,
                pdf_artifact_uri=pdf_artifact_uri,
            )
        )
    return tuple(spans)
