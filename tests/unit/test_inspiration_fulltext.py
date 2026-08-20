from __future__ import annotations

import json
from pathlib import Path

import pytest

from material_agent.inspiration.fulltext import (
    LawfulFullTextResolver,
    OpenAccessLocationV1,
    UnpaywallPublicAdapter,
    parse_grobid_tei_spans,
)
from material_agent.retrieval.storage import LocalArtifactStore


class _Transport:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> bytes:
        self.calls.append({"url": url, **kwargs})
        return self.payload


def _unpaywall_payload() -> bytes:
    return json.dumps(
        {
            "doi": "10.1000/example",
            "is_oa": True,
            "best_oa_location": {
                "url_for_pdf": "https://repository.example/paper.pdf",
                "url_for_landing_page": "https://repository.example/item/1",
                "host_type": "repository",
                "version": "acceptedVersion",
                "license": "cc-by",
            },
        }
    ).encode()


def _tei() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text><body><div><head>Electronic structure</head>
    <p coords="3,10,20,100,12"><s coords="3,10,20,50,12">The isolated band has a width of 42 meV.</s><s coords="3,60,20,50,12">Ti d and ligand p states hybridize.</s></p>
  </div></body></text>
</TEI>"""


def test_unpaywall_adapter_returns_only_reported_oa_pdf_location() -> None:
    transport = _Transport(_unpaywall_payload())
    location, raw = UnpaywallPublicAdapter(
        email_resolver=lambda: "researcher@example.org",
        transport=transport,
    ).resolve("10.1000/EXAMPLE")

    assert raw == _unpaywall_payload()
    assert location is not None
    assert location.pdf_url == "https://repository.example/paper.pdf"
    assert location.license == "cc-by"
    assert "email=researcher%40example.org" in str(transport.calls[0]["url"])


def test_grobid_tei_parser_emits_section_sentence_page_locators() -> None:
    spans = parse_grobid_tei_spans(
        _tei(),
        document_id="document-" + "1" * 24,
        evidence_id="evidence-" + "2" * 24,
        pdf_artifact_uri="artifact://research/paper.pdf",
        tei_artifact_uri="artifact://research/paper.tei.xml",
    )

    assert len(spans) == 2
    assert spans[0].section_path == ("Electronic structure",)
    assert spans[0].paragraph_index == 1
    assert spans[0].sentence_index == 1
    assert spans[0].page_numbers == (3,)
    assert "width of 42 meV" in spans[0].text_excerpt


def test_fulltext_resolver_persists_unpaywall_pdf_tei_and_spans(
    tmp_path: Path,
) -> None:
    class PdfFetcher:
        def fetch(self, location: OpenAccessLocationV1) -> bytes:
            assert location.license == "cc-by"
            return b"%PDF-1.7\nfixture"

    class Grobid:
        def process_fulltext(
            self, pdf_payload: bytes, *, max_response_bytes: int
        ) -> bytes:
            assert pdf_payload.startswith(b"%PDF-")
            assert max_response_bytes >= len(_tei())
            return _tei()

    resolver = LawfulFullTextResolver(
        unpaywall=UnpaywallPublicAdapter(
            email_resolver=lambda: "researcher@example.org",
            transport=_Transport(_unpaywall_payload()),
        ),
        pdf_fetcher=PdfFetcher(),  # type: ignore[arg-type]
        grobid=Grobid(),
        store=LocalArtifactStore(tmp_path),
        run_id="fulltext-run",
    )

    result = resolver.resolve(
        doi="10.1000/example",
        document_id="document-" + "1" * 24,
        evidence_id="evidence-" + "2" * 24,
    )

    assert result.status == "RESOLVED"
    assert len(result.spans) == 2
    for uri in (
        result.unpaywall_artifact_uri,
        result.pdf_artifact_uri,
        result.tei_artifact_uri,
    ):
        assert uri is not None
        assert (tmp_path / uri.removeprefix("artifact://")).is_file()


def test_tei_parser_rejects_doctype() -> None:
    with pytest.raises(ValueError, match="unsafe TEI"):
        parse_grobid_tei_spans(
            b'<!DOCTYPE TEI [<!ENTITY x "bad">]><TEI/>',
            document_id="document-" + "1" * 24,
            evidence_id="evidence-" + "2" * 24,
            pdf_artifact_uri="artifact://research/paper.pdf",
            tei_artifact_uri="artifact://research/paper.tei.xml",
        )
