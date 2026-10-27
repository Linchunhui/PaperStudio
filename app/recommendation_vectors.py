"""Versioned, normalized vector ingestion and cached exact retrieval.

The model runs outside the HTTP request. Paper and interest vectors must share
one explicitly activated model space. Stale document hashes are never served.
"""
from collections import OrderedDict
import hashlib
import json
import threading
import numpy as np
from fastapi import HTTPException
from . import auth, core, taxonomy
from .recommendation_content import DOCUMENT_VERSION, RetrievedPaper, embedding_document

QUERY_VERSION = "research-interest-v1"
_LOCK = threading.RLock()
_CACHE = OrderedDict()


def init_vectors():
    with core.db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS recommendation_structured (
            catalog_paper_id TEXT PRIMARY KEY REFERENCES catalog_papers(id) ON DELETE CASCADE,
            data TEXT NOT NULL, source_version TEXT NOT NULL, source_document_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS recommendation_vector_spaces (
            id TEXT PRIMARY KEY, model TEXT NOT NULL, model_revision TEXT NOT NULL,
            dimensions INTEGER NOT NULL, document_version TEXT NOT NULL, query_version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS recommendation_vectors (
            space_id TEXT NOT NULL REFERENCES recommendation_vector_spaces(id),
            entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL, input_hash TEXT NOT NULL,
            vector BLOB NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(space_id,entity_kind,entity_id));
        CREATE TABLE IF NOT EXISTS recommendation_vector_state (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), active_space TEXT,
            revision INTEGER NOT NULL DEFAULT 0);
        INSERT OR IGNORE INTO recommendation_vector_state(singleton,revision) VALUES(1,0);
        """)


def interest_document(kind, identifier):
    if kind == "topic" and identifier in taxonomy.TOPICS:
        category, topic = taxonomy.TOPICS[identifier]
        text = f"Research interest: {category['name']} / {topic['name']}\n{topic['description']}"
    elif kind == "category" and identifier in taxonomy.CATEGORIES:
        category = taxonomy.CATEGORIES[identifier]
        text = f"Research interest: {category['name']}\n" + "\n".join(t["name"] + ": " + t["description"] for t in category["topics"])
    else:
        raise HTTPException(422, "向量引用了未知研究方向")
    return {"entity_kind": kind, "entity_id": identifier, "text": text, "role": "query",
            "template_version": QUERY_VERSION, "input_hash": hashlib.sha256((QUERY_VERSION + "\n" + text).encode()).hexdigest()}


def import_vectors(body):
    values = body.model_dump()
    spec = (values["space_id"], values["model"], values["model_revision"], values["dimensions"], values["document_version"], values["query_version"])
    if spec[4:] != (DOCUMENT_VERSION, QUERY_VERSION):
        raise HTTPException(422, "向量输入模板版本不匹配，请重新导出输入")
    with core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        old = con.execute("SELECT * FROM recommendation_vector_spaces WHERE id=?", (spec[0],)).fetchone()
        if old and tuple(old) != spec:
            raise HTTPException(409, "同一向量空间不能混用模型、维度或输入模板，请使用新 space_id")
        con.execute("INSERT OR IGNORE INTO recommendation_vector_spaces VALUES(?,?,?,?,?,?)", spec)
        seen = set()
        for item in values["items"]:
            key = (item["entity_kind"], item["entity_id"])
            if key in seen:
                raise HTTPException(422, "同一批次包含重复向量")
            seen.add(key)
            if key[0] == "paper":
                paper = con.execute("SELECT data FROM catalog_papers WHERE id=?", (key[1],)).fetchone()
                if not paper:
                    raise HTTPException(404, "向量引用的目录论文不存在")
                expected = embedding_document(json.loads(paper[0]))["text_hash"]
            else:
                expected = interest_document(*key)["input_hash"]
            if item["input_hash"] != expected:
                raise HTTPException(409, "论文或方向文本已变化，请使用最新输入重新生成向量")
            vector = np.asarray(item["vector"], dtype=np.float64)
            if vector.shape != (spec[3],) or not np.isfinite(vector).all():
                raise HTTPException(422, "向量维度错误或包含非有限值")
            norm = np.linalg.norm(vector)
            if not np.isfinite(norm) or norm <= 1e-12:
                raise HTTPException(422, "向量范数无效")
            blob = (vector / norm).astype("<f4").tobytes()
            con.execute("INSERT INTO recommendation_vectors VALUES(?,?,?,?,?,?) ON CONFLICT(space_id,entity_kind,entity_id) DO UPDATE SET input_hash=excluded.input_hash,vector=excluded.vector,updated_at=excluded.updated_at",
                        (spec[0], *key, expected, blob, core.now()))
        if values["activate"]:
            _require_ready_space(con, spec[0])
        con.execute("UPDATE recommendation_vector_state SET revision=revision+1,active_space=CASE WHEN ? THEN ? ELSE active_space END WHERE singleton=1", (values["activate"], spec[0]))
    return {"imported": len(seen), "space_id": spec[0], "activated": values["activate"]}


def _require_ready_space(con, space_id):
    kinds = {r[0] for r in con.execute("SELECT DISTINCT entity_kind FROM recommendation_vectors WHERE space_id=?", (space_id,))}
    if "paper" not in kinds or not kinds.intersection({"category", "topic"}):
        raise HTTPException(409, "启用前至少需要论文向量和研究方向向量；请先分批导入")


def activate_space(space_id):
    with core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        if not con.execute("SELECT 1 FROM recommendation_vector_spaces WHERE id=?", (space_id,)).fetchone():
            raise HTTPException(404, "向量空间不存在")
        _require_ready_space(con, space_id)
        con.execute("UPDATE recommendation_vector_state SET active_space=?,revision=revision+1 WHERE singleton=1", (space_id,))
    return {"active_space": space_id}


def invalidate_paper(con, paper_id, data=None):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='recommendation_vectors'").fetchone():
        return
    query = "DELETE FROM recommendation_vectors WHERE entity_kind='paper' AND entity_id=?"
    args = [paper_id]
    if data is not None:
        query += " AND input_hash!=?"
        args.append(embedding_document(data)["text_hash"])
    if con.execute(query, args).rowcount:
        con.execute("UPDATE recommendation_vector_state SET revision=revision+1 WHERE singleton=1")
    query = "DELETE FROM recommendation_structured WHERE catalog_paper_id=?"
    if data is not None:
        query += " AND source_document_hash!=?"
    con.execute(query, args)


def store_structured(paper_id, body):
    with core.db() as con:
        paper = con.execute("SELECT data FROM catalog_papers WHERE id=?", (paper_id,)).fetchone()
        if not paper:
            raise HTTPException(404, "目录论文不存在")
        if body.source_document_hash != embedding_document(json.loads(paper[0]))["text_hash"]:
            raise HTTPException(409, "论文文本已变化，请重新核对结构化结果")
        con.execute("INSERT INTO recommendation_structured VALUES(?,?,?,?,?) ON CONFLICT(catalog_paper_id) DO UPDATE SET data=excluded.data,source_version=excluded.source_version,source_document_hash=excluded.source_document_hash,updated_at=excluded.updated_at",
                    (paper_id, json.dumps(body.model_dump(exclude={"source_version", "source_document_hash"}), ensure_ascii=False), body.source_version, body.source_document_hash, core.now()))
    return {"catalog_paper_id": paper_id, "saved": True}


def _index():
    with core.db() as con:
        state = con.execute("SELECT active_space,revision FROM recommendation_vector_state WHERE singleton=1").fetchone()
        if not state or not state[0]:
            return None
        space = con.execute("SELECT * FROM recommendation_vector_spaces WHERE id=?", (state[0],)).fetchone()
        key = (str(core.DATA_DIR.resolve()), state[0], state[1], taxonomy.VERSION)
        with _LOCK:
            if key in _CACHE:
                _CACHE.move_to_end(key)
                return _CACHE[key]
        rows = con.execute("SELECT * FROM recommendation_vectors WHERE space_id=? ORDER BY entity_kind,entity_id", (state[0],)).fetchall()
    ids, vectors, interests = [], [], {}
    for row in rows:
        vector = np.frombuffer(row["vector"], dtype="<f4")
        if len(vector) != space["dimensions"]:
            continue
        if row["entity_kind"] == "paper":
            ids.append(row["entity_id"])
            vectors.append(vector)
        else:
            try:
                expected = interest_document(row["entity_kind"], row["entity_id"])["input_hash"]
            except HTTPException:
                continue
            if row["input_hash"] == expected:
                interests[(row["entity_kind"], row["entity_id"])] = vector
    value = (ids, np.stack(vectors) if vectors else np.empty((0, space["dimensions"]), dtype=np.float32), interests, space["id"])
    with _LOCK:
        _CACHE[key] = value
        while len(_CACHE) > 2:
            _CACHE.popitem(last=False)
    return value


class EmbeddingProvider:
    name = "embedding"

    def retrieve(self, context, limit):
        index = _index()
        if index is None:
            return []
        ids, matrix, interests, _ = index
        keys = [(kind, identifier) for kind, field in (("topic", "topic_ids"), ("category", "category_ids")) for identifier in context.profile[field]]
        queries = [interests[key] for key in keys if key in interests]
        if not queries or not ids:
            return []
        # Apply page filters BEFORE top-k, then the orchestrator validates again.
        from .recommendations import _filter_conditions
        where, args = _filter_conditions(context.category_id, context.topic_id)
        where.append("NOT EXISTS(SELECT 1 FROM recommendation_dismissals d WHERE d.catalog_paper_id=p.id AND d.user_id=?)")
        args.append(auth.current_user_id())
        with core.db() as con:
            allowed = {r[0] for r in con.execute("SELECT p.id FROM catalog_papers p WHERE " + " AND ".join(where), args)}
        positions = np.asarray([i for i, identifier in enumerate(ids) if identifier in allowed], dtype=int)
        if not len(positions):
            return []
        # One query per interest; do not average unrelated research interests.
        scores = matrix[positions] @ np.stack(queries).T
        per_interest = max(1, limit // len(queries))
        chosen = set()
        for column in range(scores.shape[1]):
            chosen.update(np.argsort(-scores[:, column], kind="stable")[:per_interest].tolist())
        best = scores.max(axis=1)
        ordered = sorted((i for i in chosen if best[i] > 0), key=lambda i: (-float(best[i]), ids[positions[i]]))[:limit]
        return [RetrievedPaper(ids[positions[i]], max(0.0, min(1.0, float(best[i])))) for i in ordered]


def status():
    with core.db() as con:
        state = con.execute("SELECT active_space,revision FROM recommendation_vector_state WHERE singleton=1").fetchone()
        rows = con.execute("SELECT space_id,entity_kind,count(*) count FROM recommendation_vectors GROUP BY space_id,entity_kind").fetchall()
        structured = con.execute("SELECT count(*) FROM recommendation_structured").fetchone()[0]
    return {"active_space": state[0] if state else None, "revision": state[1] if state else 0,
            "vectors": [dict(row) for row in rows], "structured_papers": structured}
