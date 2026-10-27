"""Versioned two-level research labels. Metadata aids, never exclusion rules."""
from collections import Counter
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import unicodedata

DOCUMENT = json.loads((Path(__file__).resolve().parent.parent / "config/directions.json").read_text())
VERSION = DOCUMENT["version"] + ":keyword-v2-" + hashlib.sha256(
    json.dumps(DOCUMENT, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
CATEGORIES = {c["id"]: c for c in DOCUMENT["categories"]}
TOPICS = {t["id"]: (c, t) for c in CATEGORIES.values() for t in c["topics"]}
UNKNOWN = {"category_id": "unclassified", "category_name": "未分类", "topic_id": None, "topic_name": "待确认二级方向"}
# Broad previous labels remain searchable through the old direction facet.
# They can establish a parent, but never invent a specific child assignment.
LEGACY = {
    "大语言模型与智能体": "nlp", "自然语言处理": "nlp", "多模态与视觉语言": "multimodal",
    "计算机视觉": "vision", "生成模型": "generation", "机器人与具身智能": "robotics",
    "强化学习与决策": "rl", "机器学习理论与优化": "theory", "模型效率与系统": "efficiency",
    "安全、对齐与隐私": "trust", "科学与医疗AI": "science", "图学习与推荐": "retrieval",
    "时间序列与预测": "science", "OCR": "document", "文档智能": "document",
}
ALIASES = {
    "multimodal.vqa": ("vqa", "vlm", "mllm", "视觉问答", "多模态理解"),
    "document.ocr": ("ocr", "文字识别", "文本检测"),
    "document.layout": ("文档解析", "版面分析", "pdf parsing"),
    "document.docqa": ("文档问答", "文档理解"),
    "document.table": ("表格识别", "公式识别", "图表理解"),
    "nlp.reason": ("数学推理", "思维链"), "nlp.agent": ("智能体", "工具调用"),
    "efficiency.adapt": ("lora", "on policy distillation", "opd", "知识蒸馏", "参数高效微调"),
    "rl.preference": ("grpo", "dpo", "偏好优化", "奖励模型"),
    "robotics.vla": ("vla", "模仿学习"), "generation.discrete": ("diffusion llm", "扩散语言模型"),
    "retrieval.rag": ("rag", "检索增强生成"), "retrieval.recommend": ("推荐系统",),
}


def normalize(text):
    return re.sub(r"\s+", " ", re.sub(r"[-_‐‑–—]", " ", unicodedata.normalize("NFKC", str(text or "")).casefold())).strip()


def _expression(pattern):
    # The supplied file contains both literal phrases and escaped \b markers.
    text = normalize(pattern.replace("\\\\", "\\").replace(r"\b", ""))
    escaped = re.escape(text)
    if re.search(r"[a-z]{4,}$", text) and not text.endswith("s"):
        escaped += "s?"
    # CJK aliases may occur without whitespace in a Chinese sentence.
    return (r"(?<![a-z0-9])" if text[:1].isascii() else "") + escaped + (r"(?![a-z0-9])" if text[-1:].isascii() else "")


PATTERNS = {key: re.compile("|".join(_expression(p) for p in [*topic["patterns"], *ALIASES.get(key, ())]))
            for key, (_, topic) in TOPICS.items()}
PARENTS = {
    "nlp": ("language model", "llm", "nlp", "自然语言"),
    "multimodal": ("multimodal", "vision language", "多模态"),
    "document": ("document intelligence", "文档智能"),
    "vision": ("computer vision", "visual learning", "计算机视觉"),
    "generation": ("diffusion", "generative", "生成模型"),
    "geometry": ("3d", "三维"), "robotics": ("robot", "robotic", "embodied", "机器人"),
    "rl": ("reinforcement learning", "强化学习"), "retrieval": ("retrieval", "检索"),
    "efficiency": ("model efficiency", "efficient inference"), "trust": ("ai safety", "可信"),
}
PARENT_PATTERNS = {key: re.compile("|".join(_expression(p) for p in phrases)) for key, phrases in PARENTS.items()}


def label(topic_id):
    category, topic = TOPICS[topic_id]
    return {"category_id": category["id"], "category_name": category["name"], "topic_id": topic_id, "topic_name": topic["name"]}


@lru_cache(maxsize=4096)
def _classify(title, abstract, legacy):
    texts = {"title": normalize(title), "abstract": normalize(abstract)}
    ranked = []
    for key, pattern in PATTERNS.items():
        evidence = [{"field": field, "phrase": phrase} for field, text in texts.items()
                    for phrase in sorted({m.group(0) for m in pattern.finditer(text)})]
        if evidence:
            score = sum((4 if e["field"] == "title" else 1) * min(5, len(e["phrase"].split()) + 1) for e in evidence)
            ranked.append((score, key, evidence))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    assignments = [dict(label(key), score=score, evidence=evidence) for score, key, evidence in ranked[:3]]
    if assignments:
        primary, secondary = assignments[0], assignments[1:]
    else:
        parent = next((key for key, pattern in PARENT_PATTERNS.items() if pattern.search(texts["title"])), None)
        parent = parent or next((key for key, pattern in PARENT_PATTERNS.items() if pattern.search(texts["abstract"])), None)
        parent = parent or next((LEGACY[value] for value in legacy if value in LEGACY), None)
        primary = {**UNKNOWN, **({"category_id": parent, "category_name": CATEGORIES[parent]["name"]} if parent else {})}
        secondary = []
    return {"version": VERSION, "method": "title-abstract-keywords", "status": "heuristic-unverified",
            "input": "title-and-abstract" if abstract else "title", "primary": primary, "secondary": secondary,
            "needs_review": True, "unassigned_topic": not primary["topic_id"],
            "note": "主方向及最多两个关联方向为元数据初标，待确认；未分类记录仍保留。"}


def classify(title, abstract="", legacy=()):
    # Return an independent value so imports cannot mutate cached labels.
    return json.loads(json.dumps(_classify(str(title or ""), str(abstract or ""), tuple(legacy))))


def facets(classification):
    primary = classification["primary"]
    values = {("primary_category", primary["category_id"])}
    if primary["topic_id"]:
        values.add(("primary_topic", primary["topic_id"]))
    for item in [primary, *classification["secondary"]]:
        values.add(("category", item["category_id"]))
        if item["topic_id"]:
            values.add(("topic", item["topic_id"]))
    return values


def category_id(value):
    if value == "unclassified" or value == "未分类":
        return "unclassified"
    return value if value in CATEGORIES else next((c["id"] for c in CATEGORIES.values() if c["name"] == value), None)


def topic_id(value):
    return value if value in TOPICS else next((key for key, (_, t) in TOPICS.items() if t["name"] == value), None)


def matches(item, direction="", topic=""):
    values = facets(item["classification"])
    category = category_id(direction)
    parent_matches = not direction or direction == "all"
    parent_matches = parent_matches or (("category", category) in values if category else direction in item.get("directions", []))
    child = topic_id(topic)
    if child and category and TOPICS[child][0]["id"] != category:
        return False
    return parent_matches and (not topic or topic == "all" or ("topic", child) in values)


def snapshot(counts=None):
    counts = counts or {}
    categories = []
    for category in [*CATEGORIES.values(), {"id": "unclassified", "name": "未分类", "topics": []}]:
        key = category["id"]
        categories.append({"id": key, "name": category["name"], "count": counts.get(("primary_category", key), 0),
                           "match_count": counts.get(("category", key), 0), "topics": [
                               {"id": t["id"], "name": t["name"], "description": t["description"],
                                "count": counts.get(("primary_topic", t["id"]), 0), "match_count": counts.get(("topic", t["id"]), 0)}
                               for t in category["topics"]]})
    return {"version": VERSION, "categories": categories, "count_scope": "exclusive-primary", "filter_scope": "primary-and-secondary",
            "policy": DOCUMENT["policy"]}


def item_snapshot(items):
    counts = Counter(pair for item in items for pair in facets(item["classification"]))
    return snapshot(counts)
