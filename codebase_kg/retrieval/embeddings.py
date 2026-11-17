"""OpenAI embeddings through LangChain.

The API key is read from settings and is never written to logs by this module.
"""

from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Small surface used by indexing and retrieval."""

    dimensions: int
    secrets: list[str]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of chunk texts."""

    def embed_query(self, text: str) -> list[float]:
        """Embed one natural-language question."""


class OpenAIEmbeddingProvider:
    """LangChain ``OpenAIEmbeddings`` adapter.

    Imports are deferred so unit tests that never construct this class do not
    need to talk to OpenAI.
    """

    def __init__(self, api_key: str, model: str, dimensions: int) -> None:
        from langchain_openai import OpenAIEmbeddings

        self.dimensions = dimensions
        self.secrets = [api_key]
        self._client = OpenAIEmbeddings(model=model, api_key=api_key, dimensions=dimensions)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 64):
            batch = texts[start : start + 64]
            vectors.extend(self._client.embed_documents(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return list(self._client.embed_query(text))
