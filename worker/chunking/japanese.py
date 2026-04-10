import fugashi
from models import Chunk

# Module-level singleton — MeCab tagger is expensive to initialise
tagger = fugashi.Tagger()


def chunk_japanese(text: str, chunk_size: int, overlap: int) -> list[Chunk]:
    """
    Tokenize Japanese text with MeCab via fugashi, then slide a window of
    chunk_size tokens with the given overlap. Tokens are joined without spaces
    (correct for Japanese). Returns a list of Chunk dataclass instances.
    """
    tokens = [w.surface for w in tagger(text)]
    chunks: list[Chunk] = []
    start = 0
    index = 0
    step = chunk_size - overlap

    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunk_text = "".join(chunk_tokens)

        chunks.append(Chunk(
            text=chunk_text,
            index=index,
            token_count=len(chunk_tokens),
        ))

        start += step
        index += 1

    return chunks
