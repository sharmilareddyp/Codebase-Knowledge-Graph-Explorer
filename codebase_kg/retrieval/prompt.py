"""Prompt text for grounded answers.

The retrieval modules select evidence. This prompt only instructs the model how
to talk about that evidence. Citations returned by the API are taken from the
retrieved records, not parsed out of the model's prose.
"""

from __future__ import annotations

from codebase_kg.models import Evidence

STATIC_ANALYSIS_NOTE = (
    "Static analysis cannot reliably resolve every dynamic Python call, "
    "including getattr, callbacks, reflection, super(), most calls on local instances, "
    "and decorators that replace functions."
)

SYSTEM_PROMPT = """You answer questions about a Python codebase using only the evidence provided.
The evidence comes from static analysis and semantic search. The indexed code was not executed.

Rules:
- Cite the repository, file path, entity name, and line range for every claim.
- Use the supplied callers, callees, unresolved calls, imports, and file locations.
- If the evidence is insufficient, start the answer with INSUFFICIENT_EVIDENCE and say what is missing.
- Do not guess about behavior that the evidence does not support.
- Static analysis cannot reliably resolve every dynamic Python call, including getattr, callbacks, reflection, super(), most calls on local instances, and decorators that replace functions.
"""


def format_evidence(evidence: list[Evidence]) -> str:
    """Render evidence blocks the answerer can quote."""

    blocks: list[str] = []
    for index, item in enumerate(evidence, start=1):
        lines = [
            f"[{index}] repository: {item.repository_name} ({item.repository_id})",
            f"entity: {item.name}",
            f"qualified_name: {item.qualified_name}",
            f"kind: {item.kind}",
            f"file: {item.file_path}",
            f"lines: {item.start_line}-{item.end_line}",
        ]
        if item.signature:
            lines.append(f"signature: {item.signature}")
        if item.docstring:
            lines.append("docstring:")
            lines.append(item.docstring[:1000])
        if item.excerpt:
            lines.append("excerpt:")
            lines.append(item.excerpt[:1500])
        if item.callers:
            lines.append("callers: " + "; ".join(item.callers[:15]))
        if item.callees:
            lines.append("callees: " + "; ".join(item.callees[:15]))
        if item.unresolved_calls:
            lines.append("unresolved_calls: " + "; ".join(item.unresolved_calls[:15]))
        if item.imports:
            lines.append("imports: " + "; ".join(item.imports[:15]))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def interpret_answer(text: str) -> tuple[str, bool]:
    """Split the model's insufficient-evidence marker from the user-facing answer."""

    if "INSUFFICIENT_EVIDENCE" in text:
        cleaned = text.replace("INSUFFICIENT_EVIDENCE", "").strip()
        if not cleaned:
            cleaned = "The indexed evidence is insufficient to answer this question."
        return cleaned, False
    cleaned = text.strip()
    if not cleaned:
        return "The indexed evidence is insufficient to answer this question.", False
    return cleaned, True
