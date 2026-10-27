"""Stable content contract for future offline embeddings and candidate adapters.

No model is loaded and no network request is made by the recommendation path.
Generated interpretations, translations and private notes are separate assets.
"""
from dataclasses import dataclass
import hashlib
from typing import Protocol

DOCUMENT_VERSION = "paper-title-abstract-v1"


def embedding_document(paper):
    title = str(paper.get("title") or "").strip()
    abstract = str(paper.get("abstract") or "").strip()
    text = f"Title: {title}" + (f"\n\nAbstract: {abstract}" if abstract else "")
    return {"catalog_paper_id": paper["id"], "text": text,
            "input_type": "title_abstract" if abstract else "title_only",
            "template_version": DOCUMENT_VERSION,
            "text_hash": hashlib.sha256((DOCUMENT_VERSION + "\n" + text).encode()).hexdigest()}


@dataclass(frozen=True)
class RetrievalContext:
    profile: dict
    category_id: str = ""
    topic_id: str = ""


@dataclass(frozen=True)
class RetrievedPaper:
    catalog_paper_id: str
    # Optional normalized semantic relevance, not a click probability.
    relevance: float | None = None


class CandidateProvider(Protocol):
    name: str

    def retrieve(self, context: RetrievalContext, limit: int) -> list[RetrievedPaper]:
        """Future embedding/structured/Gorse adapters return public catalog IDs.

        Each adapter owns its timeout and index/model version checks. All IDs
        are validated and exclusions/filters reapplied by the orchestrator.
        """
        ...
