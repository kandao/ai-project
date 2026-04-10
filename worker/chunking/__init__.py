from chunking.english import chunk_english
from chunking.japanese import chunk_japanese
from config import settings


def chunk(text: str, language: str) -> list:
    if language == "ja":
        return chunk_japanese(text, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP)
    return chunk_english(text, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP)
