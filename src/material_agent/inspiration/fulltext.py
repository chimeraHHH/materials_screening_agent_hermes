"""Lawful OA full-text resolution and GROBID TEI evidence localization."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Callable, Mapping
from typing import Protocol
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from pydantic import Field

from material_agent.inspiration.models import canonical_json_bytes
from material_agent.inspiration.research_graph import FineGrainedEvidenceSpanV1
from material_agent.inspiration.search import (
    BoundedHttpTransport,
    SearchAdapterError,
    UrlLibBoundedTransport,
    normalize_doi,
)
from material_agent.orchestrator.models import StrictModel
from material_agent.retrieval.storage import LocalArtifactStore

UNPAYWALL_EMAIL_ENV = "UNPAYWALL_EMAIL"
GROBID_BASE_URL_ENV = "GROBID_BASE_URL"
_UNPAYWALL_HOST = "api.unpaywall.org"
_TEI_NS = "http://www.tei-c.org/ns/1.0"
_MAX_PDF_BYTES = 10_000_000
_MAX_TEI_BYTES = 10_000_000


class OpenAccessLocationV1(StrictModel):
    doi: str = Field(min_length=6, max_length=256)
    pdf_url: str = Field(min_length=8, max_length=2_048)
    landing_page_url: str | None = Field(default=None, max_length=2_048)
    host_type: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    license: str | None = Field(default=None, max_length=128)
    provenance: str = "UNPAYWALL_BEST_OA_LOCATION"


class FullTextResolutionV1(StrictModel):
    doi: str
    document_id: str
    evidence_id: str
    status: str
    oa_location: OpenAccessLocationV1 | None = None
    unpaywall_artifact_uri: str | None = None
    pdf_artifact_uri: str | None = None
    tei_artifact_uri: str | None = None
    spans: tuple[FineGrainedEvidenceSpanV1, ...] = Field(default=(), max_length=256)
    failure_category: str | None = Field(default=None, max_length=128)


class GrobidTransport(Protocol):
    def process_fulltext(
        self, pdf_payload: bytes, *, max_response_bytes: int
    ) -> bytes: ...


class UnpaywallPublicAdapter:
    """Resolve an explicitly reported OA PDF location for one DOI."""

    def __init__(
        self,
        *,
        email_resolver: Callable[[], str] | None = None,
        transport: BoundedHttpTransport | None = None,
    ) -> None:
        self.email_resolver = email_resolver or (
            lambda: os.environ.get(UNPAYWALL_EMAIL_ENV, "")
        )
        self.transport = transport or UrlLibBoundedTransport(
            allowed_hosts=frozenset({_UNPAYWALL_HOST})
        )

    def resolve(
        self, doi: str, *, max_response_bytes: int = 1_000_000
    ) -> tuple[OpenAccessLocationV1 | None, bytes]:
        normalized = normalize_doi(doi)
        if normalized is None:
            raise ValueError("Unpaywall resolution requires a DOI")
        email = self.email_resolver().strip()
        if (
            not email
            or len(email) > 254
            or "@" not in email
            or any(character.isspace() for character in email)
        ):
            raise SearchAdapterError(
                "UNPAYWALL_EMAIL_UNAVAILABLE",
                "Unpaywall requires a valid contact email in UNPAYWALL_EMAIL",
            )
        url = (
            f"https://{_UNPAYWALL_HOST}/v2/{quote(normalized, safe='/')}?"
            + urlencode({"email": email})
        )
        result = self.transport.get(
            url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "materials-screening-agent/0.1 (oa-resolution)",
            },
            timeout_seconds=20.0,
            max_response_bytes=max_response_bytes,
            max_physical_requests=3,
        )
        payload = result if isinstance(result, bytes) else result.payload
        location = parse_unpaywall_location(payload, expected_doi=normalized)
        return location, payload


def parse_unpaywall_location(
    payload: bytes, *, expected_doi: str
) -> OpenAccessLocationV1 | None:
    try:
        record = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SearchAdapterError(
            "INVALID_JSON", "Unpaywall response is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(record, Mapping):
        raise SearchAdapterError("SCHEMA_DRIFT", "Unpaywall response must be an object")
    if normalize_doi(record.get("doi") if isinstance(record.get("doi"), str) else None) != (
        expected_doi
    ):
        raise SearchAdapterError("PROVENANCE_MISMATCH", "Unpaywall DOI differs")
    if record.get("is_oa") is not True:
        return None
    best = record.get("best_oa_location")
    if best is None:
        return None
    if not isinstance(best, Mapping):
        raise SearchAdapterError(
            "SCHEMA_DRIFT", "best_oa_location must be an object or null"
        )
    pdf_url = best.get("url_for_pdf")
    if not isinstance(pdf_url, str) or not _valid_https_url(pdf_url):
        return None
    host_type = best.get("host_type")
    version = best.get("version")
    if not isinstance(host_type, str) or not isinstance(version, str):
        raise SearchAdapterError(
            "SCHEMA_DRIFT", "OA location host_type/version are required"
        )
    landing = best.get("url_for_landing_page")
    license_value = best.get("license")
    return OpenAccessLocationV1(
        doi=expected_doi,
        pdf_url=pdf_url,
        landing_page_url=(
            landing if isinstance(landing, str) and _valid_https_url(landing) else None
        ),
        host_type=host_type,
        version=version,
        license=license_value if isinstance(license_value, str) else None,
    )


class OpenAccessPdfFetcher:
    """Fetch only the exact HTTPS host reported by Unpaywall."""

    def __init__(
        self, transport_factory: Callable[[str], BoundedHttpTransport] | None = None
    ) -> None:
        self.transport_factory = transport_factory or (
            lambda host: UrlLibBoundedTransport(
                allowed_hosts=frozenset({host}),
                allowed_media_types=frozenset(
                    {"application/pdf", "application/octet-stream"}
                ),
            )
        )

    def fetch(self, location: OpenAccessLocationV1) -> bytes:
        host = urlsplit(location.pdf_url).hostname
        if host is None:
            raise SearchAdapterError("INVALID_URL", "OA PDF URL has no host")
        result = self.transport_factory(host.casefold()).get(
            location.pdf_url,
            headers={
                "Accept": "application/pdf",
                "Accept-Encoding": "identity",
                "User-Agent": "materials-screening-agent/0.1 (oa-pdf-fetch)",
            },
            timeout_seconds=60.0,
            max_response_bytes=_MAX_PDF_BYTES,
            max_physical_requests=3,
        )
        payload = result if isinstance(result, bytes) else result.payload
        if not payload.startswith(b"%PDF-"):
            raise SearchAdapterError("INVALID_PDF", "OA location did not return a PDF")
        return payload


class UrllibLocalGrobidTransport:
    """Multipart GROBID client restricted to an explicitly local service."""

    def __init__(self, base_url: str | None = None, *, timeout_seconds: float = 120.0):
        selected = (base_url or os.environ.get(GROBID_BASE_URL_ENV, "http://127.0.0.1:8070")).rstrip(
            "/"
        )
        parsed = urlsplit(selected)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("GROBID_BASE_URL must target a local loopback service")
        self.base_url = selected
        self.timeout_seconds = timeout_seconds

    def process_fulltext(
        self, pdf_payload: bytes, *, max_response_bytes: int
    ) -> bytes:
        if not pdf_payload.startswith(b"%PDF-") or len(pdf_payload) > _MAX_PDF_BYTES:
            raise ValueError("GROBID input must be a bounded PDF")
        boundary = "----HermesGrobid" + secrets.token_hex(12)
        body = _multipart_body(boundary, pdf_payload)
        request = Request(
            f"{self.base_url}/api/processFulltextDocument",
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/xml",
                "User-Agent": "materials-screening-agent/0.1 (local-grobid)",
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
            payload = response.read(max_response_bytes + 1)
        if len(payload) > max_response_bytes:
            raise ValueError("GROBID TEI exceeded the response byte budget")
        return payload


class LawfulFullTextResolver:
    def __init__(
        self,
        *,
        unpaywall: UnpaywallPublicAdapter,
        pdf_fetcher: OpenAccessPdfFetcher,
        grobid: GrobidTransport,
        store: LocalArtifactStore,
        run_id: str,
    ) -> None:
        self.unpaywall = unpaywall
        self.pdf_fetcher = pdf_fetcher
        self.grobid = grobid
        self.store = store
        self.run_id = run_id

    def resolve(
        self, *, doi: str, document_id: str, evidence_id: str
    ) -> FullTextResolutionV1:
        normalized = normalize_doi(doi)
        if normalized is None:
            raise ValueError("full-text resolution requires a DOI")
        try:
            location, raw = self.unpaywall.resolve(normalized)
            raw_ref = self.store.write_bytes(
                f"research/{self.run_id}/fulltext/{evidence_id}-unpaywall.json",
                raw,
                "application/json",
                immutable=True,
            )
            if location is None:
                return FullTextResolutionV1(
                    doi=normalized,
                    document_id=document_id,
                    evidence_id=evidence_id,
                    status="UNAVAILABLE",
                    unpaywall_artifact_uri=raw_ref.uri,
                    failure_category="NO_OPEN_ACCESS_PDF",
                )
            pdf = self.pdf_fetcher.fetch(location)
            pdf_ref = self.store.write_bytes(
                f"research/{self.run_id}/fulltext/{evidence_id}.pdf",
                pdf,
                "application/pdf",
                immutable=True,
            )
            tei = self.grobid.process_fulltext(pdf, max_response_bytes=_MAX_TEI_BYTES)
            tei_ref = self.store.write_bytes(
                f"research/{self.run_id}/fulltext/{evidence_id}.tei.xml",
                tei,
                "application/xml",
                immutable=True,
            )
            spans = parse_grobid_tei_spans(
                tei,
                document_id=document_id,
                evidence_id=evidence_id,
                pdf_artifact_uri=pdf_ref.uri,
                tei_artifact_uri=tei_ref.uri,
            )
            if not spans:
                raise ValueError("GROBID returned no body evidence spans")
            return FullTextResolutionV1(
                doi=normalized,
                document_id=document_id,
                evidence_id=evidence_id,
                status="RESOLVED",
                oa_location=location,
                unpaywall_artifact_uri=raw_ref.uri,
                pdf_artifact_uri=pdf_ref.uri,
                tei_artifact_uri=tei_ref.uri,
                spans=spans,
            )
        except (OSError, RuntimeError, SearchAdapterError, ValueError) as exc:
            return FullTextResolutionV1(
                doi=normalized,
                document_id=document_id,
                evidence_id=evidence_id,
                status="UNAVAILABLE",
                failure_category=type(exc).__name__,
            )


def parse_grobid_tei_spans(
    payload: bytes,
    *,
    document_id: str,
    evidence_id: str,
    pdf_artifact_uri: str,
    tei_artifact_uri: str,
    max_spans: int = 256,
) -> tuple[FineGrainedEvidenceSpanV1, ...]:
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("unsafe TEI declarations are forbidden")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ValueError("GROBID returned invalid TEI XML") from exc
    body = root.find(f".//{{{_TEI_NS}}}body")
    if body is None:
        return ()
    spans: list[FineGrainedEvidenceSpanV1] = []
    paragraph_index = 0
    for div in body.iter(f"{{{_TEI_NS}}}div"):
        headings = tuple(
            text
            for node in div.findall(f"./{{{_TEI_NS}}}head")
            if (text := _element_text(node))
        )
        for paragraph in div.findall(f"./{{{_TEI_NS}}}p"):
            paragraph_index += 1
            sentences = paragraph.findall(f".//{{{_TEI_NS}}}s")
            nodes = sentences or [paragraph]
            for sentence_index, node in enumerate(nodes, start=1):
                text = _element_text(node)
                if not text:
                    continue
                pages = _coords_pages(node.get("coords") or paragraph.get("coords"))
                locator = (
                    f"section={' > '.join(headings) or 'body'};"
                    f"paragraph={paragraph_index};sentence={sentence_index};"
                    f"pages={','.join(str(page) for page in pages) or 'unknown'}"
                )
                text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
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
                        section_path=headings,
                        paragraph_index=paragraph_index,
                        sentence_index=sentence_index if sentences else None,
                        page_numbers=pages,
                        text_excerpt=text[:2_000],
                        text_sha256=text_hash,
                        locator=locator,
                        parser="GROBID_TEI",
                        tei_artifact_uri=tei_artifact_uri,
                        pdf_artifact_uri=pdf_artifact_uri,
                    )
                )
                if len(spans) >= max_spans:
                    return tuple(spans)
    return tuple(spans)


def _multipart_body(boundary: str, pdf_payload: bytes) -> bytes:
    fields = (
        ("segmentSentences", "1"),
        ("generateIDs", "1"),
        ("teiCoordinates", "p"),
        ("teiCoordinates", "s"),
        ("teiCoordinates", "figure"),
    )
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="input"; filename="paper.pdf"\r\n',
            b"Content-Type: application/pdf\r\n\r\n",
            pdf_payload,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks)


def _element_text(node: ElementTree.Element) -> str:
    return " ".join("".join(node.itertext()).split())


def _coords_pages(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    pages: list[int] = []
    for box in value.split(";"):
        first = box.split(",", 1)[0].strip()
        if first.isdigit() and (page := int(first)) > 0 and page not in pages:
            pages.append(page)
    return tuple(pages)


def _valid_https_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username
