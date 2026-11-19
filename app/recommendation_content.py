"""Stable content contract for future offline embeddings and candidate adapters.

No model is loaded and no network request is made by the recommendation path.
Generated interpretations, translations and private notes are separate assets.
"""
from dataclasses import dataclass
import hashlib
import json
from typing import Protocol

DOCUMENT_VERSION = "paper-title-abstract-v1"


def presentation(con, paper):
    """Public catalog interpretation only; never consult personal research jobs."""
    from .reading_cards import presentation as reading_card
    supplied = reading_card(paper)
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='recommendation_structured'").fetchone():
        return {"abstract": paper.get("abstract") or "", "structured": None, **supplied}
    row = con.execute("SELECT * FROM recommendation_structured WHERE catalog_paper_id=?",
                      (paper["id"],)).fetchone()
    structured = None
    if row and row["source_document_hash"] == embedding_document(paper)["text_hash"]:
        data = json.loads(row["data"])
        structured = {key: data.get(key, "" if key == "research_problem" else []) for key in
                      ("research_problem", "tasks", "methods", "contributions", "experiments", "datasets", "limitations", "evidence")}
        structured.update(source_version=row["source_version"], updated_at=row["updated_at"],
                          source_document_hash=row["source_document_hash"], evidence_scope="catalog-material")
    return {"abstract": paper.get("abstract") or "", "structured": structured, **supplied}


def paper_presentation(paper_id, project_id):
    from . import core
    project_id = core.get_project(project_id)["id"]
    core.require_membership(paper_id, project_id)
    with core.db() as con:
        row = con.execute("SELECT c.data FROM catalog_papers c JOIN paper_catalog_links l ON l.catalog_paper_id=c.id WHERE l.paper_id=? AND l.match_method='verified-source'", (paper_id,)).fetchone()
        if not row:
            return {"catalog_paper_id": None, "abstract": "", "structured": None}
        paper = json.loads(row[0])
        return {"catalog_paper_id": paper["id"], **presentation(con, paper)}


def _library_catalog(con, local, tables):
    """Resolve verified links, or a unique accessible exact-PDF duplicate.

    Titles, filenames and unverified source aliases are never identity evidence.
    A different account's inaccessible paper cannot supply the duplicate link.
    """
    from . import core
    from fastapi import HTTPException
    if not {'catalog_papers', 'paper_catalog_links'} <= tables:
        return None
    row = con.execute("SELECT c.id,c.data FROM catalog_papers c JOIN paper_catalog_links l ON l.catalog_paper_id=c.id WHERE l.paper_id=? AND l.match_method='verified-source'", (local['id'],)).fetchone()
    if row:
        return {**json.loads(row['data']), 'id': row['id']}
    digest = local.get('sha256')
    if not isinstance(digest, str) or len(digest) != 64:
        return None
    matches = {}
    for row in con.execute("SELECT p.id AS paper_id,c.id,c.data FROM papers p JOIN paper_catalog_links l ON l.paper_id=p.id JOIN catalog_papers c ON c.id=l.catalog_paper_id WHERE p.sha256=? AND l.match_method='verified-source'", (digest,)):
        try:
            core.get_paper(row['paper_id'])
        except HTTPException:
            continue
        matches[row['id']] = {**json.loads(row['data']), 'id': row['id']}
    return next(iter(matches.values())) if len(matches) == 1 else None


def library_cards(papers):
    """Add public card fields to rows already authorized by the library route.

    Local identity, custom titles, permissions and per-project progress win over
    public metadata. Model/network jobs and private corpus adapters are not used.
    """
    from . import core, paper_figures
    if not papers:
        return []
    # An allowlist prevents arbitrary public metadata from injecting library state.
    fields = ('authors', 'institutions', 'published_at', 'year', 'arxiv_id', 'doi',
              'url', 'pdf_url', 'github_url', 'hf_upvotes', 'github_stars',
              'classification', 'conferences', 'provenance', 'kind')
    result = []
    with core.db() as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for local in papers:
            # Recheck authorization for callers other than the library list route.
            core.get_paper(local['id'])
            public = _library_catalog(con, local, tables)
            content = {}
            if public:
                content = {key: public[key] for key in fields if key in public}
                content.update(presentation(con, public))
                card = content.get('reading_card')
                # Reading cards may describe another PDF revision of the same ID.
                if card and card.get('source_sha256') != local.get('sha256'):
                    content.pop('reading_card', None)
                # A catalog entry can have several accessible PDF revisions.
                # Do not show a different revision's image on this library card.
                linked = paper_figures._linked(public['id'])
                if linked and linked['sha256'] == local.get('sha256'):
                    content.update(paper_figures.presentation(public))
                content['catalog_paper_id'] = public['id']
            result.append({**content, **local, 'paper_id': local['id']})
    return result


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
