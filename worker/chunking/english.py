from models import Chunk


def chunk_english(text: str, chunk_size: int, overlap: int) -> list[Chunk]:
    """
    Split text by whitespace into words, then slide a window of chunk_size
    words with the given overlap. Returns a list of Chunk dataclass instances.
    """
    words = text.split()
    chunks: list[Chunk] = []
    start = 0
    index = 0
    step = chunk_size - overlap

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk_words = words[start:end]
        chunk_text = " ".join(chunk_words)

        chunks.append(Chunk(
            text=chunk_text,
            index=index,
            token_count=len(chunk_words),
        ))

        start += step
        index += 1

    return chunks
