from extractors.pdf import extract_pdf
from extractors.docx import extract_docx
from extractors.text import extract_text

EXTRACTORS = {
    "pdf": extract_pdf,
    "docx": extract_docx,
    "txt": extract_text,
    "md": extract_text,
}


def extract(file_path: str, file_type: str) -> str:
    fn = EXTRACTORS.get(file_type.lower())
    if not fn:
        raise ValueError(f"Unsupported file type: {file_type}")
    return fn(file_path)
