import pdfplumber


def extract_pdf(file_path: str) -> str:
    """Extract text from a PDF file using pdfplumber."""
    text_parts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text is None:
                continue
            text_parts.append(page_text)
    return "\n\n".join(text_parts)
