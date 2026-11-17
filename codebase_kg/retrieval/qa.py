"""Semantic retrieval plus a LangChain answer step.

Graph reads and embedding calls stay in small methods so tests can replace
them. LangChain is used to turn the already-retrieved evidence into an answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from codebase_kg.errors import AnswerError, EmbeddingsUnavailable, NotFoundError
from codebase_kg.logging_config import redact
from codebase_kg.models import Evidence
from codebase_kg.retrieval.embeddings import EmbeddingProvider
from codebase_kg.retrieval.prompt import SYSTEM_PROMPT, format_evidence, interpret_answer

logger = logging.getLogger(__name__)


class Answerer(Protocol):
    """Produces a grounded answer from evidence that was already retrieved."""

    def answer(self, question: str, evidence: list[Evidence]) -> str:
        """Return the model text, including the insufficient-evidence marker when needed."""


class LangChainAnswerer:
    """Orchestrate a chat model with the grounded system prompt."""

    def __init__(self, api_key: str, model: str) -> None:
        from langchain_core.output_parsers import StrOutputParser
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_openai import ChatOpenAI

        self.secrets = [api_key]
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", SYSTEM_PROMPT),
                ("human", "Question:\n{question}\n\nEvidence:\n{evidence}"),
            ]
        )
        self._chain = prompt | ChatOpenAI(model=model, api_key=api_key, temperature=0) | StrOutputParser()

    def answer(self, question: str, evidence: list[Evidence]) -> str:
        return str(self._chain.invoke({"question": question, "evidence": format_evidence(evidence)}))


@dataclass
class QueryResult:
    """Answer plus the evidence records the API should cite."""

    answer: str
    evidence: list[Evidence]
    sufficient_evidence: bool


class QAService:
    """Retrieve code evidence for one repository and answer from that evidence only."""

    def __init__(
        self,
        store: object,
        embedder: EmbeddingProvider | None,
        answerer: Answerer | None,
        min_score: float,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.answerer = answerer
        self.min_score = min_score

    def ask(self, repo_id: str, question: str, top_k: int) -> QueryResult:
        """Answer ``question`` using semantic search and graph context.

        When nothing is retrieved, the language model is not called.
        """

        if self.embedder is None or self.answerer is None:
            raise EmbeddingsUnavailable(
                "OpenAI credentials are not configured. Set OPENAI_API_KEY to enable natural-language queries."
            )
        repository = self.store.get_repository(repo_id)  # type: ignore[attr-defined]
        if repository is None:
            raise NotFoundError("Repository not found")

        try:
            vector = self.embedder.embed_query(question)
        except Exception as exc:
            logger.warning("Embedding query failed: %s", redact(str(exc), _secrets(self.embedder)))
            raise AnswerError("The embedding provider failed.") from exc

        hits = self.store.similarity_search(repo_id, vector, top_k, self.min_score)  # type: ignore[attr-defined]
        evidence = _enrich(self.store, repository.summary.name, hits)
        if not evidence:
            return QueryResult(
                answer="The indexed evidence is insufficient to answer this question.",
                evidence=[],
                sufficient_evidence=False,
            )
        try:
            raw = self.answerer.answer(question, evidence)
        except Exception as exc:
            logger.warning("Answer provider failed: %s", redact(str(exc), _secrets(self.answerer)))
            raise AnswerError("The answer provider failed.") from exc
        text, sufficient = interpret_answer(raw)
        return QueryResult(answer=text, evidence=evidence, sufficient_evidence=sufficient)


def _enrich(store: object, repository_name: str, hits: list[object]) -> list[Evidence]:
    evidence: list[Evidence] = []
    for hit in hits:
        detail = store.get_entity(hit.entity_id)  # type: ignore[attr-defined]
        if detail is None:
            continue
        summary = detail.summary
        evidence.append(
            Evidence(
                repository_id=summary.repository_id,
                repository_name=repository_name,
                entity_id=summary.id,
                kind=summary.kind,
                name=summary.name,
                qualified_name=summary.qualified_name,
                file_path=summary.file_path,
                start_line=summary.start_line,
                end_line=summary.end_line,
                docstring=summary.docstring,
                signature=summary.signature,
                excerpt=str(getattr(hit, "text", "") or ""),
                score=float(getattr(hit, "score", 0.0)),
                callers=[_format_call(item) for item in detail.callers[:15]],
                callees=[_format_call(item) for item in detail.callees[:15]],
                unresolved_calls=[f"{item.raw_name} ({item.status})" for item in detail.unresolved_calls[:15]],
                imports=[item.statement for item in detail.imports[:15]],
            )
        )
    return evidence


def _format_call(item: object) -> str:
    return (
        f"{item.qualified_name} ({item.file_path}:{item.lineno})"  # type: ignore[attr-defined]
    )


def _secrets(component: object) -> list[str]:
    values = getattr(component, "secrets", [])
    return [str(item) for item in values if item]
