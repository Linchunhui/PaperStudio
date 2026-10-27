"""Recommendation APIs share the existing session and CSRF boundary."""
import json
from typing import Annotated, Literal
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from . import auth, core, recommendations, taxonomy
from . import recommendation_profiles as profiles
from .recommendation_content import embedding_document
from . import recommendation_vectors as vectors

router = APIRouter(prefix="/api", tags=["recommendations"])
Short = Annotated[str, Field(min_length=1, max_length=200)]


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_ids: list[Short] = Field(default_factory=list, max_length=30)
    topic_ids: list[Short] = Field(default_factory=list, max_length=100)
    institutions: list[Short] = Field(default_factory=list, max_length=100)
    conferences: list[Short] = Field(default_factory=list, max_length=100)
    keywords: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=20)
    freshness_days: int = Field(default=30, ge=1, le=365)
    expected_version: int = Field(ge=0)


class FollowInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["institution", "conference"]
    value: Short
    enabled: bool


class StructuredInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_version: Short
    source_document_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    research_problem: str = Field(default="", max_length=4000)
    tasks: list[Short] = Field(default_factory=list, max_length=50)
    methods: list[Short] = Field(default_factory=list, max_length=50)
    contributions: list[Annotated[str, Field(max_length=2000)]] = Field(default_factory=list, max_length=20)
    datasets: list[Short] = Field(default_factory=list, max_length=50)
    limitations: list[Annotated[str, Field(max_length=2000)]] = Field(default_factory=list, max_length=20)
    evidence: list[Annotated[str, Field(max_length=2000)]] = Field(default_factory=list, max_length=50)


class VectorItem(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    entity_kind: Literal["paper", "category", "topic"]
    entity_id: Short
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    vector: list[float] = Field(min_length=1, max_length=4096)


class VectorBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space_id: Short
    model: Short
    model_revision: Short
    dimensions: int = Field(ge=1, le=4096)
    document_version: str = "paper-title-abstract-v1"
    query_version: str = "research-interest-v1"
    activate: bool = False
    items: list[VectorItem] = Field(min_length=1, max_length=100)


@router.get("/me/recommendation-profile")
def profile():
    return profiles.get_profile()


@router.put("/me/recommendation-profile")
def update_profile(body: ProfileInput):
    return profiles.save_profile(body.model_dump(exclude={"expected_version"}), body.expected_version)


@router.patch("/me/recommendation-profile/follow")
def follow(body: FollowInput):
    return profiles.set_follow(body.kind, body.value, body.enabled)


@router.get("/recommendations/options")
def options():
    with core.db() as con:
        institutions = [r[0] for r in con.execute("SELECT DISTINCT name FROM catalog_institutions ORDER BY name")]
        conferences = [r[0] for r in con.execute("SELECT DISTINCT name FROM catalog_conferences ORDER BY name")]
    return {"taxonomy": taxonomy.snapshot(), "institutions": institutions, "conferences": conferences}


@router.get("/recommendations")
def feed(limit: int = Query(default=24, ge=1, le=60), cursor: str = Query(default="", max_length=100), category_id: str = Query(default="", max_length=100), topic_id: str = Query(default="", max_length=100)):
    return recommendations.recommend(limit=limit, cursor=cursor, category_id=category_id, topic_id=topic_id)


@router.post("/recommendations/{catalog_paper_id}/dismiss")
def dismiss(catalog_paper_id: str):
    profiles.dismiss(catalog_paper_id)
    return {"dismissed": True}


@router.delete("/me/recommendation-dismissals")
def restore():
    profiles.restore_dismissed()
    return {"restored": True}


@router.get("/admin/recommendations/embedding-inputs")
def embedding_inputs(after: str = Query(default="", max_length=100), limit: int = Query(default=100, ge=1, le=500)):
    auth.require_admin()
    with core.db() as con:
        rows = con.execute("SELECT data FROM catalog_papers WHERE id>? ORDER BY id LIMIT ?", (after, limit + 1)).fetchall()
    items = [embedding_document(json.loads(row[0])) for row in rows[:limit]]
    return {"items": [{**item, "entity_kind": "paper", "entity_id": item["catalog_paper_id"], "role": "document", "input_hash": item["text_hash"]} for item in items], "next_after": items[-1]["catalog_paper_id"] if len(rows) > limit else None}


@router.get("/admin/recommendations/interest-inputs")
def interest_inputs():
    auth.require_admin()
    return {"items": [vectors.interest_document(kind, key) for kind, entries in (("category", taxonomy.CATEGORIES), ("topic", taxonomy.TOPICS)) for key in entries]}


@router.post("/admin/recommendations/vectors")
def upload_vectors(body: VectorBatch):
    auth.require_admin()
    return vectors.import_vectors(body)


@router.post("/admin/recommendations/spaces/{space_id}/activate")
def activate(space_id: str):
    auth.require_admin()
    return vectors.activate_space(space_id)


@router.put("/admin/recommendations/content/{catalog_paper_id}")
def structured(catalog_paper_id: str, body: StructuredInput):
    auth.require_admin()
    return vectors.store_structured(catalog_paper_id, body)


@router.get("/admin/recommendations/status")
def readiness():
    auth.require_admin()
    return vectors.status()
