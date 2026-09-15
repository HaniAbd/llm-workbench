"""Turning text into vectors, via the same Ollama endpoint the chat model uses.

The embedding model is deliberately not the chat model. `llama3.2` can produce
vectors, but they are a side effect of a model trained to continue text, not to
place similar meanings near each other - and at 3072 dimensions they exceed
pgvector's 2000-dimension index limit. `nomic-embed-text` is 768-dimensional
and trained for retrieval.

Vectors from different models are not comparable. Changing EMBEDDING_MODEL
means re-indexing everything; `index_docs.py` records the model it used so a
mismatch is visible rather than silently returning nonsense neighbours.
"""

import os

from dotenv import load_dotenv
from openai import OpenAI

# Loaded here as well as in main.py so the indexing CLI works without going
# through the app. Same relative path, same requirement to run from api/.
load_dotenv(dotenv_path="../.env")

_client = OpenAI(
    base_url=os.environ["OPENAI_BASE_URL"],
    api_key=os.environ["OPENAI_API_KEY"],
)

MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
DIM = int(os.environ.get("EMBEDDING_DIM", "768"))


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch. Order of the result matches the order of the input."""
    if not texts:
        return []
    response = _client.embeddings.create(model=MODEL, input=texts)
    vectors = [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
    for v in vectors:
        if len(v) != DIM:
            raise ValueError(
                f"{MODEL} returned {len(v)} dimensions, but EMBEDDING_DIM is {DIM}. "
                "Fix the env var and re-index; stored vectors of a different width "
                "cannot be compared."
            )
    return vectors


def embed_one(text: str) -> list[float]:
    return embed([text])[0]
