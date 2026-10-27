"""Account-owned recommendation preferences, independent of model availability."""
import json
from fastapi import HTTPException
from . import auth, core, taxonomy

LIST_FIELDS = ("category_ids", "topic_ids", "institutions", "conferences", "authors", "keywords")


def init_recommendations():
    with core.db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS recommendation_profiles (
            user_id TEXT PRIMARY KEY, data TEXT NOT NULL, version INTEGER NOT NULL,
            updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS recommendation_dismissals (
            user_id TEXT NOT NULL, catalog_paper_id TEXT NOT NULL
                REFERENCES catalog_papers(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL, PRIMARY KEY(user_id,catalog_paper_id));
        CREATE TABLE IF NOT EXISTS recommendation_snapshots (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, profile_version INTEGER NOT NULL,
            data TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS recommendation_snapshot_owner
            ON recommendation_snapshots(user_id,created_at);
        CREATE TABLE IF NOT EXISTS paper_catalog_links (
            paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
            catalog_paper_id TEXT NOT NULL REFERENCES catalog_papers(id) ON DELETE CASCADE,
            match_method TEXT NOT NULL, created_at TEXT NOT NULL);
        """)


def get_profile():
    with core.db() as con:
        row = con.execute("SELECT * FROM recommendation_profiles WHERE user_id=?", (auth.current_user_id(),)).fetchone()
    defaults = {**{key: [] for key in LIST_FIELDS}, "freshness_days": 30}
    return {**defaults, **(json.loads(row["data"]) if row else {}),
            "version": row["version"] if row else 0, "updated_at": row["updated_at"] if row else None,
            "configured": bool(row)}


def _clean(data):
    result = {key: list(dict.fromkeys(" ".join(value.split()) for value in data.get(key, []) if value.strip())) for key in LIST_FIELDS}
    if any(value not in taxonomy.CATEGORIES for value in result["category_ids"]):
        raise HTTPException(422, "一级方向不存在，请刷新方向列表")
    if any(value not in taxonomy.TOPICS for value in result["topic_ids"]):
        raise HTTPException(422, "二级方向不存在，请刷新方向列表")
    # Selecting a child does not implicitly subscribe to all of its siblings.
    result["freshness_days"] = data.get("freshness_days", 30)
    return result


def save_profile(data, expected_version):
    clean = _clean(data)
    user_id, stamp = auth.current_user_id(), core.now()
    with core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        old = con.execute("SELECT version FROM recommendation_profiles WHERE user_id=?", (user_id,)).fetchone()
        version = old["version"] if old else 0
        if expected_version != version:
            raise HTTPException(409, "推荐偏好已在其他页面更新，请重新载入后再保存")
        con.execute("INSERT INTO recommendation_profiles VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data,version=excluded.version,updated_at=excluded.updated_at",
                    (user_id, json.dumps(clean, ensure_ascii=False), version + 1, stamp))
        con.execute("DELETE FROM recommendation_snapshots WHERE user_id=?", (user_id,))
    return get_profile()


def set_follow(kind, value, enabled):
    field = {"institution": "institutions", "conference": "conferences", "author": "authors", "category": "category_ids", "topic": "topic_ids"}[kind]
    # Retry only a concurrent profile write; never replace unrelated preferences.
    for _ in range(3):
        profile = get_profile()
        values = [v for v in profile[field] if v.casefold() != value.casefold()]
        if enabled:
            values.append(value)
        if len(values) > 100:
            raise HTTPException(422, "每类最多关注 100 项")
        profile[field] = values
        try:
            return save_profile(profile, profile["version"])
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
    raise HTTPException(409, "偏好正在更新，请重试")


def dismiss(catalog_paper_id):
    with core.db() as con:
        if not con.execute("SELECT 1 FROM catalog_papers WHERE id=?", (catalog_paper_id,)).fetchone():
            raise HTTPException(404, "目录论文不存在")
        con.execute("INSERT OR IGNORE INTO recommendation_dismissals VALUES(?,?,?)", (auth.current_user_id(), catalog_paper_id, core.now()))


def restore_dismissed():
    with core.db() as con:
        con.execute("DELETE FROM recommendation_dismissals WHERE user_id=?", (auth.current_user_id(),))
        con.execute("DELETE FROM recommendation_snapshots WHERE user_id=?", (auth.current_user_id(),))


def merge_catalog_identity(con, old_id, new_id):
    """Called inside catalog's merge transaction before deleting the old row."""
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "recommendation_structured" in tables:
        from .recommendation_content import embedding_document
        paper = con.execute("SELECT data FROM catalog_papers WHERE id=?", (new_id,)).fetchone()
        if paper:
            con.execute("INSERT OR IGNORE INTO recommendation_structured SELECT ?,data,source_version,source_document_hash,updated_at FROM recommendation_structured WHERE catalog_paper_id=? AND source_document_hash=?", (new_id, old_id, embedding_document(json.loads(paper[0]))["text_hash"]))
    if "recommendation_vectors" in tables:
        from .recommendation_vectors import invalidate_paper
        invalidate_paper(con, old_id)
    if "recommendation_dismissals" in tables:
        con.execute("INSERT OR IGNORE INTO recommendation_dismissals SELECT user_id,?,created_at FROM recommendation_dismissals WHERE catalog_paper_id=?", (new_id, old_id))
    if "paper_catalog_links" in tables:
        con.execute("UPDATE paper_catalog_links SET catalog_paper_id=? WHERE catalog_paper_id=?", (new_id, old_id))
    if "recommendation_snapshots" in tables:
        con.execute("DELETE FROM recommendation_snapshots")


def link_paper(paper_id, catalog_paper_id):
    core.get_paper(paper_id)
    with core.db() as con:
        if not con.execute("SELECT 1 FROM catalog_papers WHERE id=?", (catalog_paper_id,)).fetchone():
            return False  # Catalog may have been merged while a download ran.
        con.execute("INSERT INTO paper_catalog_links VALUES(?,?,?,?) ON CONFLICT(paper_id) DO UPDATE SET catalog_paper_id=excluded.catalog_paper_id,match_method=excluded.match_method",
                    (paper_id, catalog_paper_id, "verified-source", core.now()))
    return True


def verify_catalog_source(source, catalog_paper_id):
    """Verify the public identifier; a client-provided ID/title is not evidence."""
    from .source_resolver import resolve_source
    with core.db() as con:
        row = con.execute("SELECT data FROM catalog_papers WHERE id=?", (catalog_paper_id,)).fetchone()
    if not row:
        raise HTTPException(404, "目录论文不存在，请刷新推荐")
    paper = json.loads(row[0])
    resolution = paper.get("resolve") or {}
    if resolution.get("action") != "search":
        for url in (resolution.get("url"), paper.get("pdf_url"), paper.get("url")):
            if not url:
                continue
            try:
                expected = resolve_source(url)
            except HTTPException:
                continue
            if (expected["download_url"] == source.get("download_url") or
                expected.get("arxiv_id") and expected["arxiv_id"] == source.get("arxiv_id")):
                return catalog_paper_id
    raise HTTPException(422, "导入链接与目录论文不匹配，请从最新目录重新打开")
