"""Cold-start catalog retrieval, explainable ranking and account-bound paging."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import re

from fastapi import HTTPException
from . import auth, core, taxonomy
from .recommendation_content import CandidateProvider, RetrievalContext, presentation
from .recommendation_profiles import get_profile

logger = logging.getLogger(__name__)
STRATEGY_VERSION = "catalog-hybrid-v1"
MAX_CANDIDATES = 480
SNAPSHOT_MINUTES = 15


def _timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _freshness(paper, at, half_life):
    # Institution seed dates may be conference dates; do not call them new papers.
    uncertain = any(p.get("source_id") == "institution" and "日期可能" in p.get("notes", "") for p in paper.get("provenance", []))
    reliable_other = any(p.get("source_id") == "hf" for p in paper.get("provenance", []))
    published = _timestamp(paper.get("published_at"))
    if not published or published > at or (uncertain and not reliable_other):
        return 0.0, None
    days = (at - published).total_seconds() / 86400
    return 2 ** (-days / half_life), days


def _facet_condition(kind, values):
    return ("EXISTS(SELECT 1 FROM catalog_facets f WHERE f.paper_id=p.id AND f.kind=? AND f.value IN (" + ",".join("?" for _ in values) + "))", [kind, *values])


def _filter_conditions(category_id, topic_id):
    where, args = [], []
    for kind, value in (("category", category_id), ("topic", topic_id)):
        if value:
            term, params = _facet_condition(kind, [value])
            where.append(term)
            args.extend(params)
    return where, args


def _validate_filters(category_id, topic_id):
    if category_id and category_id not in taxonomy.CATEGORIES and category_id != "unclassified":
        raise HTTPException(422, "未知一级方向")
    if topic_id and topic_id not in taxonomy.TOPICS:
        raise HTTPException(422, "未知二级方向")
    if category_id and topic_id and taxonomy.TOPICS[topic_id][0]["id"] != category_id:
        raise HTTPException(422, "二级方向不属于所选一级方向")


def _retrieve(con, profile, category_id, topic_id, providers):
    where, args = _filter_conditions(category_id, topic_id)
    where.append("NOT EXISTS(SELECT 1 FROM recommendation_dismissals d WHERE d.catalog_paper_id=p.id AND d.user_id=?)")
    args.append(auth.current_user_id())
    candidates, channels = {}, {}

    def add(condition="1=1", parameters=(), channel="recent", limit=60, order="recent"):
        ordering = "CASE WHEN p.published_at<=? THEN p.published_at ELSE NULL END DESC,p.id" if order == "recent" else "p.hf_upvotes IS NULL,p.hf_upvotes DESC,p.id"
        ordering_args = [core.now()] if order == "recent" else []
        rows = con.execute("SELECT p.data FROM catalog_papers p WHERE " + " AND ".join([*where, condition]) + " ORDER BY " + ordering + " LIMIT ?", [*args, *parameters, *ordering_args, limit]).fetchall()
        for row in rows:
            item = json.loads(row[0])
            candidates[item["id"]] = item
            channels.setdefault(item["id"], set()).add(channel)

    # Round-robin budgets per interest prevent one broad topic swallowing retrieval.
    interests = [("topic", v) for v in profile["topic_ids"]] + [("category", v) for v in profile["category_ids"]]
    per_interest = max(4, min(80, 240 // max(1, len(interests))))
    for kind, value in interests:
        condition, params = _facet_condition(kind, [value])
        add(condition, params, "interest", per_interest)
        add(condition, params, "interest", max(2, per_interest // 4), "popular")
    for field, table, channel in (("institutions", "catalog_institutions", "institution"), ("conferences", "catalog_conferences", "conference")):
        values = profile[field]
        if values:
            condition = f"EXISTS(SELECT 1 FROM {table} x WHERE x.paper_id=p.id AND x.name COLLATE NOCASE IN (" + ",".join("?" for _ in values) + "))"
            add(condition, values, channel, 120)
    if profile["keywords"]:
        parts, params = [], []
        for term in profile["keywords"]:
            parts.append("p.search_text LIKE ? ESCAPE '\\'")
            params.append("%" + term.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        add("(" + " OR ".join(parts) + ")", params, "keyword", 120)
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='recommendation_structured'").fetchone():
            structured_parts = ["lower(s.data) LIKE ? ESCAPE '\\'" for _ in params]
            add("EXISTS(SELECT 1 FROM recommendation_structured s WHERE s.catalog_paper_id=p.id AND (" + " OR ".join(structured_parts) + "))", params, "structured", 80)
    degraded = []
    semantic = {}
    for provider in providers:
        try:
            rows = provider.retrieve(RetrievalContext(profile, category_id, topic_id), 120)
            valid = {r.catalog_paper_id: r.relevance for r in list(rows)[:120]
                     if isinstance(r.catalog_paper_id, str) and (r.relevance is None or isinstance(r.relevance, (float, int)) and math.isfinite(r.relevance) and 0 <= r.relevance <= 1)}
            if valid:
                add("p.id IN (" + ",".join("?" for _ in valid) + ")", list(valid), provider.name, 120)
                semantic.update({key: score for key, score in valid.items() if score is not None and key in candidates})
        except Exception:
            # Provider errors must not take reading or the rule fallback down.
            degraded.append(provider.name)
            logger.warning("Recommendation candidate provider unavailable: %s", provider.name)
    has_preferences = any(profile[key] for key in ("category_ids", "topic_ids", "keywords", "institutions", "conferences"))
    fallback = "no_matches" if has_preferences and not candidates else "no_preferences" if not has_preferences else None
    if fallback:
        # Preserve explicit page filters even when the preference pool is empty.
        add(limit=240)
        add(channel="catalog", limit=60, order="popular")
    if candidates and con.execute("SELECT 1 FROM sqlite_master WHERE name='recommendation_structured'").fetchone():
        keys = list(candidates)
        for start in range(0, len(keys), 400):
            batch = keys[start:start + 400]
            for row in con.execute("SELECT catalog_paper_id,data FROM recommendation_structured WHERE catalog_paper_id IN (" + ",".join("?" for _ in batch) + ")", batch):
                candidates[row[0]]["structured"] = json.loads(row[1])
    return candidates, channels, semantic, fallback, degraded


def _rank(item, profile, channels, semantic, at):
    classification = item.get("classification") or taxonomy.classify(item["title"], item.get("abstract", ""))
    labels = [classification["primary"], *classification.get("secondary", [])]
    topic_labels = [v for v in labels if v.get("topic_id") in profile["topic_ids"]]
    category_labels = [v for v in labels if v.get("category_id") in profile["category_ids"]]
    text = taxonomy.normalize(item["title"] + " " + item.get("abstract", ""))
    keywords = [term for term in profile["keywords"] if re.search(taxonomy._expression(term), text)]
    structured = item.get("structured", {})
    structured_text = taxonomy.normalize(" ".join([structured.get("research_problem", ""), *structured.get("tasks", []), *structured.get("methods", []), *structured.get("contributions", [])]))
    structured_keywords = [term for term in profile["keywords"] if re.search(taxonomy._expression(term), structured_text)]
    institutions = [value for value in item.get("institutions", []) if value.casefold() in {v.casefold() for v in profile["institutions"]}]
    conferences = sorted({c["conference"] for c in item.get("conferences", []) if c["conference"].casefold() in {v.casefold() for v in profile["conferences"]}})
    relevance = max(1.0 if topic_labels else 0, .72 if category_labels else 0, .9 if keywords else 0,
                    .85 if institutions else 0, .65 if conferences else 0, .8 if structured_keywords else 0, semantic or 0)
    freshness, age_days = _freshness(item, at, profile["freshness_days"])
    votes = item.get("hf_upvotes")
    popularity = min(1.0, math.log1p(max(0, votes)) / math.log1p(1000)) if isinstance(votes, (float, int)) and math.isfinite(votes) else 0
    reasons = []
    if topic_labels:
        reasons.append("匹配关注方向：" + "、".join(dict.fromkeys(v["topic_name"] for v in topic_labels)))
    elif category_labels:
        reasons.append("匹配关注领域：" + "、".join(dict.fromkeys(v["category_name"] for v in category_labels)))
    if keywords:
        reasons.append("匹配关键词：" + "、".join(keywords[:3]))
    if structured_keywords:
        reasons.append("研究问题或方法匹配：" + "、".join(structured_keywords[:3]))
    if institutions:
        reasons.append("匹配关注机构标签：" + "、".join(institutions[:2]))
    if conferences:
        reasons.append("来自关注会议：" + "、".join(conferences[:2]))
    if semantic is not None:
        reasons.append("论文内容与关注偏好相关")
    if age_days is not None and age_days <= profile["freshness_days"]:
        reasons.append(f"近 {profile['freshness_days']} 天发表")
    if not reasons:
        reasons.append("论文目录补充推荐")
    matched_bucket = topic_labels or category_labels
    bucket = (matched_bucket[0].get("topic_id") or matched_bucket[0]["category_id"]) if matched_bucket else classification["primary"].get("category_id", "unclassified")
    # Semantic evidence also discriminates papers sharing the same topic tag.
    return {"id": item["id"], "score": round(.55 * relevance + .10 * (semantic or 0) + .28 * freshness + .07 * popularity, 6),
            "reasons": reasons, "bucket": bucket, "channels": sorted(channels),
            "score_signals": {"relevance": relevance, "freshness": round(freshness, 4), "age_days": round(age_days, 1) if age_days is not None else None,
                              "popularity": round(popularity, 4), "semantic": semantic}}


def _diversify(rows):
    # Gentle penalty, not a hard quota that can force unrelated filler papers.
    pending = sorted(rows, key=lambda r: (-r["score"], r["id"]))[:MAX_CANDIDATES]
    result, counts = [], Counter()
    while pending:
        best = max(range(len(pending)), key=lambda i: pending[i]["score"] - .075 * counts[pending[i]["bucket"]])
        row = pending.pop(best)
        result.append(row)
        counts[row["bucket"]] += 1
        if len(result) % 24 == 0:
            counts.clear()
    return result


def recommend(*, limit=24, cursor="", category_id="", topic_id="", providers: tuple[CandidateProvider, ...] | None = None):
    _validate_filters(category_id, topic_id)
    profile, user_id = get_profile(), auth.current_user_id()
    if providers is None:
        from .recommendation_vectors import EmbeddingProvider
        providers = (EmbeddingProvider(),)
    at = _timestamp(core.now())
    with core.db() as con:
        con.execute("DELETE FROM recommendation_snapshots WHERE expires_at<=?", (at.isoformat(),))
        offset = 0
        if cursor:
            match = re.fullmatch(r"([a-f0-9]{32}):(\d{1,4})", cursor)
            if not match:
                raise HTTPException(422, "推荐分页游标无效")
            snapshot_id, offset = match[1], int(match[2])
            row = con.execute("SELECT * FROM recommendation_snapshots WHERE id=? AND user_id=?", (snapshot_id, user_id)).fetchone()
            if not row or row["profile_version"] != profile["version"]:
                raise HTTPException(409, "推荐列表已更新，请刷新后继续")
            snapshot = json.loads(row["data"])
            if snapshot["filters"] != {"category_id": category_id, "topic_id": topic_id}:
                raise HTTPException(422, "分页条件与原推荐列表不一致")
        else:
            candidates, channels, semantic, fallback, degraded = _retrieve(con, profile, category_id, topic_id, providers)
            ranked = [_rank(item, profile, channels[identifier], semantic.get(identifier), at) for identifier, item in candidates.items()]
            snapshot = {"rows": _diversify(ranked), "fallback": fallback, "degraded_sources": degraded,
                        "filters": {"category_id": category_id, "topic_id": topic_id}, "generated_at": at.isoformat(), "semantic_used": bool(semantic)}
            snapshot_id = core.uid()
            con.execute("INSERT INTO recommendation_snapshots VALUES(?,?,?,?,?,?)", (snapshot_id, user_id, profile["version"], json.dumps(snapshot, ensure_ascii=False), at.isoformat(), (at + timedelta(minutes=SNAPSHOT_MINUTES)).isoformat()))
            con.execute("DELETE FROM recommendation_snapshots WHERE user_id=? AND id NOT IN (SELECT id FROM recommendation_snapshots WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 5)", (user_id, user_id))
        rows = snapshot["rows"]
        if offset > len(rows):
            raise HTTPException(422, "推荐分页位置无效")
        items = []
        # Consume original positions, even if later dismissed/deleted; no repeats.
        while offset < len(rows) and len(items) < limit:
            entry = rows[offset]
            offset += 1
            row = con.execute("SELECT data FROM catalog_papers WHERE id=? AND NOT EXISTS(SELECT 1 FROM recommendation_dismissals WHERE user_id=? AND catalog_paper_id=?)", (entry["id"], user_id, entry["id"])).fetchone()
            if row:
                item = json.loads(row[0])
                # Keep provenance, raw source records and large abstracts out of feed responses.
                public = {key: item.get(key) for key in ("id", "title", "url", "pdf_url", "resolve", "published_at", "year", "institutions", "conferences", "classification", "source_ids", "hf_upvotes")}
                items.append({**public, **presentation(con, item), "abstract_preview": (item.get("abstract") or "")[:500],
                              "reasons": entry["reasons"], "position": offset, "score": entry["score"],
                              "score_signals": entry["score_signals"], "channels": entry["channels"]})
    return {"items": items, "next_cursor": f"{snapshot_id}:{offset}" if offset < len(rows) else None,
            "request_id": snapshot_id, "strategy_version": STRATEGY_VERSION, "profile_version": profile["version"],
            "candidate_count": len(rows), "generated_at": snapshot["generated_at"], "fallback": snapshot["fallback"],
            "degraded_sources": snapshot["degraded_sources"], "mode": "hybrid" if snapshot["semantic_used"] else "rules", "embedding_enabled": snapshot["semantic_used"]}
