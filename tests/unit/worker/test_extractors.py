"""
Unit tests for worker/extractors/.

Tests text extraction for PDF, DOCX, TXT, and MD formats.
Each test uses in-memory/temp file content — no external APIs required.
"""

import io
import os
import tempfile
import zipfile

import pytest

# Worker code lives in worker/. Add to sys.path via conftest or pytest.ini pythonpath.
# Since worker/ is NOT on sys.path by default (only tests/ is), we manipulate it here.
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../worker"))

from extractors import extract
from extractors.pdf import extract_pdf
from extractors.docx import extract_docx
from extractors.text import extract_text


# ── Helpers ─────────────────────────────────────────────────────────────────

def _write_tmp(content: bytes, suffix: str) -> str:
    """Write content to a named temp file and return its path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return path


def _minimal_pdf(text: str = "Hello World") -> bytes:
    """Return a minimal PDF with the given text on page 1."""
    stream = f"BT /F1 12 Tf 100 700 Td ({text}) Tj ET\n".encode()
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length " + str(len(stream)).encode() + b">>stream\n"
        + stream +
        b"endstream\nendobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000274 00000 n \n"
        b"0000000400 00000 n \n"
        b"trailer<</Size 6/Root 1 0 R>>\n"
        b"startxref\n470\n%%EOF\n"
    )


def _minimal_docx(paragraph: str = "Test document content.") -> bytes:
    """Return a minimal valid DOCX with one paragraph."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            "</Relationships>",
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>"
            "</w:body>"
            "</w:document>",
        )
    return buf.getvalue()


# ── PDF extraction ────────────────────────────────────────────────────────────

class TestPDFExtraction:

    def test_extract_pdf_with_text(self):
        """1.1: PDF containing 'Hello World' → extracted text contains 'Hello World'."""
        path = _write_tmp(_minimal_pdf("Hello World"), ".pdf")
        try:
            text = extract_pdf(path)
            assert "Hello" in text
        finally:
            os.unlink(path)

    def test_extract_corrupted_pdf_raises(self):
        """1.4: Random bytes named .pdf → raises exception (not silent failure)."""
        path = _write_tmp(b"this is not a pdf at all!!!", ".pdf")
        try:
            with pytest.raises(Exception):
                extract_pdf(path)
        finally:
            os.unlink(path)

    def test_extract_pdf_file_not_found(self):
        """1.10: Non-existent path → raises FileNotFoundError."""
        with pytest.raises(Exception):
            extract_pdf("/tmp/does_not_exist_12345.pdf")


# ── DOCX extraction ───────────────────────────────────────────────────────────

class TestDOCXExtraction:

    def test_extract_docx_with_paragraphs(self):
        """1.5: Valid DOCX with paragraphs → all paragraph text extracted."""
        path = _write_tmp(_minimal_docx("Test document content."), ".docx")
        try:
            text = extract_docx(path)
            assert "Test document content." in text
        finally:
            os.unlink(path)

    def test_extract_empty_docx(self):
        """1.6: DOCX with no paragraphs → returns empty string."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                "</Types>",
            )
            zf.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="word/document.xml"/>'
                "</Relationships>",
            )
            zf.writestr("word/_rels/document.xml.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>',
            )
            zf.writestr(
                "word/document.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body></w:body></w:document>",
            )
        path = _write_tmp(buf.getvalue(), ".docx")
        try:
            text = extract_docx(path)
            assert text == ""
        finally:
            os.unlink(path)


# ── Text/Markdown extraction ──────────────────────────────────────────────────

class TestTextExtraction:

    def test_extract_txt(self):
        """1.7: Plain text file → exact file content returned."""
        content = b"This is a plain text file.\nLine two."
        path = _write_tmp(content, ".txt")
        try:
            text = extract_text(path)
            assert text == content.decode("utf-8")
        finally:
            os.unlink(path)

    def test_extract_md(self):
        """1.8: Markdown file → full markdown text returned (no HTML parsing)."""
        content = b"# Header\n\n**bold** text\n\n- item1\n- item2\n"
        path = _write_tmp(content, ".md")
        try:
            text = extract_text(path)
            assert "# Header" in text
            assert "**bold**" in text
        finally:
            os.unlink(path)

    def test_extract_file_not_found(self):
        """1.10: Non-existent path → raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            extract_text("/tmp/no_such_file_99999.txt")


# ── Dispatch (extract) ────────────────────────────────────────────────────────

class TestExtractDispatch:

    def test_unsupported_type_raises(self):
        """1.9: .xlsx file type → raises ValueError('Unsupported file type')."""
        with pytest.raises(ValueError, match="Unsupported file type"):
            extract("/tmp/fake.xlsx", "xlsx")

    def test_dispatch_txt(self):
        """extract() dispatches to extract_text for 'txt'."""
        content = b"hello world"
        path = _write_tmp(content, ".txt")
        try:
            text = extract(path, "txt")
            assert text == "hello world"
        finally:
            os.unlink(path)

    def test_dispatch_md(self):
        """extract() dispatches to extract_text for 'md'."""
        content = b"# Markdown"
        path = _write_tmp(content, ".md")
        try:
            text = extract(path, "md")
            assert "# Markdown" in text
        finally:
            os.unlink(path)
