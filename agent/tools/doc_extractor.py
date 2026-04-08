"""DOCX text extraction via python-docx."""


def extract_doc(file_path: str) -> str:
    try:
        from docx import Document
        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        if not paragraphs:
            return "No text found in document."
        return "\n\n".join(paragraphs)[:50000]
    except Exception as e:
        return f"Error: {e}"
