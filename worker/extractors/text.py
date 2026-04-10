def extract_text(file_path: str) -> str:
    """Read a plain text or Markdown file with UTF-8 encoding."""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()
