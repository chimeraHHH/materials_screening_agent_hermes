from __future__ import annotations

from material_agent.inspiration.docling_parser import (
    DoclingLocalParser,
    parse_docling_markdown_spans,
)


def test_docling_markdown_parser_preserves_heading_hierarchy() -> None:
    spans = parse_docling_markdown_spans(
        "# Results\n\n## Electronic structure\n\nThe band width is 42 meV.\n\nTi d states dominate.",
        document_id="document-" + "1" * 24,
        evidence_id="evidence-" + "2" * 24,
        pdf_artifact_uri="artifact://research/paper.pdf",
        structured_artifact_uri="artifact://research/paper.docling.json",
    )

    assert len(spans) == 2
    assert spans[0].section_path == ("Results", "Electronic structure")
    assert spans[0].parser == "DOCLING"
    assert spans[0].page_numbers == ()
    assert spans[0].structured_artifact_uri.endswith("docling.json")


def test_docling_local_parser_uses_bounded_in_memory_stream() -> None:
    class Document:
        def export_to_markdown(self):
            return "# Results\n\nA compact localized state is observed."

        def export_to_dict(self):
            return {"schema_name": "DoclingDocument", "texts": ["compact"]}

    class Result:
        document = Document()

    class Converter:
        def __init__(self) -> None:
            self.calls = []

        def convert(self, source, **kwargs):
            self.calls.append((source, kwargs))
            return Result()

    converter = Converter()
    parser = DoclingLocalParser(
        converter=converter,
        stream_factory=lambda name, stream: (name, stream.read()),
    )
    markdown, structured, spans = parser.parse(
        b"%PDF-1.7\nfixture",
        document_id="document-" + "1" * 24,
        evidence_id="evidence-" + "2" * 24,
        pdf_artifact_uri="artifact://research/paper.pdf",
        structured_artifact_uri="artifact://research/paper.docling.json",
    )

    assert markdown.startswith("# Results")
    assert b"DoclingDocument" in structured
    assert len(spans) == 1
    assert converter.calls[0][1] == {
        "raises_on_error": True,
        "max_num_pages": 200,
        "max_file_size": 10_000_000,
    }
