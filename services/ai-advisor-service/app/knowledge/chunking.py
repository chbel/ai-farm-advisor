"""Split document sections into retrievable chunks with LlamaIndex.

LlamaIndex's SentenceSplitter keeps sentences intact and is given the embedding
model's own tokenizer, so chunk sizes are measured in the tokens the embedding
model actually sees (and never exceed its 512-token limit).
"""

from collections.abc import Callable
from dataclasses import dataclass

from llama_index.core.node_parser import SentenceSplitter


@dataclass(frozen=True)
class Chunk:
    location: str
    text: str


MIN_LETTER_RATIO = 0.5


def is_prose(text: str) -> bool:
    """Reject number-heavy fragments (flattened tables, indexes).

    PDF tables extract as columns of numbers without their headers; such chunks are
    unreadable as evidence and, in practice, score as similar to almost any query.
    """
    visible = [c for c in text if not c.isspace()]
    if not visible:
        return False
    return sum(c.isalpha() for c in visible) / len(visible) >= MIN_LETTER_RATIO


def make_chunks(
    sections: list[tuple[str, str]],
    tokenize: Callable[[str], list],
    chunk_tokens: int,
    overlap_tokens: int,
) -> list[Chunk]:
    splitter = SentenceSplitter(
        chunk_size=chunk_tokens, chunk_overlap=overlap_tokens, tokenizer=tokenize
    )
    chunks: list[Chunk] = []
    for location, text in sections:
        for piece in splitter.split_text(text):
            piece = piece.strip()
            # Fragments this short, or mostly digits, carry no usable information.
            if len(piece) >= 80 and is_prose(piece):
                chunks.append(Chunk(location=location, text=piece))
    return chunks
