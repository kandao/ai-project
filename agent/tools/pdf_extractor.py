"""PDF text extraction via pdfplumber."""


def extract_pdf(file_path: str) -> str:
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"--- Page {i} ---\n{page_text}")
        if not text_parts:
            return "No text found in PDF."
        return "\n\n".join(text_parts)[:50000]
    except Exception as e:
        return f"Error: {e}"
