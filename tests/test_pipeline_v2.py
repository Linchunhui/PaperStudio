import base64
import json
from pathlib import Path
import threading
import time

import fitz
import httpx
import pytest

from app import ai, auth, core, imports, jobs, parsing, providers, reading
from app.watermarks import is_boilerplate, strip_boilerplate, redact_page


def pdf_bytes(text="Original research paragraph with evidence, methods, datasets and limitations."):
    with fitz.open() as document:
        page = document.new_page(width=400, height=500)
        page.insert_text((40, 80), text)
        return document.tobytes()


@pytest.fixture
def paper(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_BASE_URL", "http://localhost:9000/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    core.init_db()
    jobs.init_jobs()
    jobs._STOP.clear()
    return core.add_paper(pdf_bytes(), "inbox", "Evidence test")


def test_codex_long_input_uses_stdin_and_restricted_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "codex")
    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.setattr(providers.shutil, "which", lambda _: "/opt/codex")
    seen = {}
    class Process:
        returncode = 0
        def __init__(self, argv, **kwargs):
            seen["argv"] = argv
            seen["input"] = kwargs["stdin"].read().decode()
            assert kwargs["shell"] is False
            assert "read-only" in argv and "features.shell_tool=false" in argv
            assert "features.apps=false" in argv and "features.hooks=false" in argv
            assert "--ephemeral" in argv and "--ignore-user-config" in argv
            Path(argv[argv.index("--output-last-message") + 1]).write_text("Answer with evidence.")
        def poll(self):
            return 0
    monkeypatch.setattr(providers.subprocess, "Popen", Process)
    cfg = ai.settings()
    assert cfg["model"] == "gpt-6-luna"
    source = "long material " * 15000
    assert ai.complete("Explain the paper.", source, cfg) == "Answer with evidence."
    assert source in seen["input"] and source not in " ".join(seen["argv"])
    assert not list((tmp_path / "model-work").iterdir())


def test_codex_cancellation_terminates_running_process(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    stopped = []
    class Process:
        returncode = None
        def __init__(self, *_args, **_kwargs):
            pass
        def poll(self):
            return self.returncode
        def terminate(self):
            stopped.append(True)
            self.returncode = -15
        def wait(self, timeout=None):
            return self.returncode
    monkeypatch.setattr(providers.subprocess, "Popen", Process)
    calls = []
    def cancel():
        calls.append(True)
        return len(calls) > 1
    cfg = {"provider": "codex", "executable": "/opt/codex", "model": "gpt-6-luna", "max_input_chars": 10000, "timeout": 10}
    with ai.request_context(cancel), pytest.raises(ai.Cancelled):
        ai.complete("system", "material", cfg)
    assert stopped


def test_vision_sends_only_embedded_images_and_checks_errors(paper, monkeypatch):
    original = httpx.Client
    seen = []
    transport = httpx.MockTransport(lambda request: seen.append(json.loads(request.content)) or httpx.Response(200, json={"choices": [{"message": {"content": "The plotted axis is x."}, "finish_reason": "stop"}]}))
    monkeypatch.setattr(providers.httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    image = "data:image/png;base64," + base64.b64encode(b"test-image").decode()
    result = ai.complete_vision("Explain this figure.", "Figure 1", [image], ai.settings())
    assert result == "The plotted axis is x."
    assert seen[0]["messages"][1]["content"][1]["image_url"]["url"] == image
    with pytest.raises(ai.ExecutionError, match="内嵌"):
        ai.complete_vision("system", "source", ["/etc/passwd"], ai.settings())


def test_babeldoc_parse_precedes_model_and_reuses_bound_cache(paper, monkeypatch):
    monkeypatch.setattr(ai, "translation_executable", lambda: "/opt/babeldoc")
    monkeypatch.setattr(ai, "complete", lambda *args: pytest.fail("parse-only must not invoke model"))
    seen = []
    class Process:
        returncode = 0
        def __init__(self, argv, **kwargs):
            seen.append(argv)
            assert "--skip-translation" in argv and "--no-auto-extract-glossary" in argv
            assert argv[argv.index("--watermark-output-mode") + 1] == "no_watermark"
            folder = Path(argv[argv.index("--working-dir") + 1])
            (folder / "styles_and_formulas.json").write_text(json.dumps({"page": [{"page_number": 0, "pdf_paragraph": [
                {"unicode": "A structured paragraph.", "box": {"x": 40, "y": 400, "x2": 200, "y2": 420}, "layout_label": "text"}]}]}))
        def poll(self):
            return 0
    monkeypatch.setattr(parsing.subprocess, "Popen", Process)
    result = parsing.ensure_parsed(paper)
    assert result["provider"] == "babeldoc" and result["source_sha"] == paper["sha256"]
    block = result["pages"][0]["blocks"][0]
    assert block["text"] == "A structured paragraph." and block["rects"][0]["y"] == pytest.approx(.16)
    assert reading._extract(paper, 1)[0]["id"] == block["id"]
    assert parsing.ensure_parsed(paper) == result and len(seen) == 1


def test_parse_fallback_is_explicit_and_cancellation_not_swallowed(paper, monkeypatch):
    monkeypatch.setattr(ai, "translation_executable", lambda: "/opt/babeldoc")
    def fail(*args):
        raise ai.ExecutionError("unsupported version")
    monkeypatch.setattr(parsing, "_babeldoc", fail)
    parsed = parsing.ensure_parsed(paper)
    assert parsed["provider"] == "pymupdf"
    assert parsed["coverage"]["warnings"] and "Original research" in parsed["fulltext"]
    parsing._cache_path(paper["sha256"]).unlink()
    with pytest.raises(ai.Cancelled):
        parsing.ensure_parsed(paper, cancelled=lambda: True)


def test_legacy_translation_reflow_has_actual_text_and_version(paper, monkeypatch):
    monkeypatch.setattr(ai, "complete", lambda *a: pytest.fail("reading an existing translation must not call AI"))
    core.attach_translation(paper["id"], pdf_bytes("Existing translated text without another model call."))
    current = core.get_paper(paper["id"])
    page = reading.page_reading(paper["id"], 1, "inbox")
    translation = page["existing_translation"]
    assert "Existing translated text" in translation["text"]
    assert translation["alignment"] == "page-only"
    assert translation["source_sha"] == current["translated_sha256"]
    assert translation["original_sha"] == current["sha256"]


def test_watermark_filter_is_specific_and_preserves_surrounding_prose():
    watermark = "本文档由 funstory.ai 的开源 PDF 翻译库 BabelDOC 38d3896 (https://github.com/funstory-ai/BabelDOC) 翻译，本仓库正在积极的建设当中，欢迎 star 和关注。"
    assert is_boilerplate(watermark)
    assert strip_boilerplate("方法内容。\n" + watermark + "\n实验结果。") == "方法内容。\n\n实验结果。"
    research = "We compare BabelDOC, the funstory.ai PDF translation library, on document structure."
    assert strip_boilerplate(research) == research


def test_workers_claim_separate_queues_without_translation_blocking(paper, monkeypatch):
    monkeypatch.setattr(ai, "translation_executable", lambda: "/opt/babeldoc")
    layout = jobs.enqueue(paper["id"], "inbox", "translate")
    text = jobs.enqueue(paper["id"], "inbox", "brief")
    block = reading._extract(paper, 1)[0]
    paragraph = jobs.enqueue(paper["id"], "inbox", "paragraph-translate", page=1, block_id=block["id"], source_sha=paper["sha256"])
    assert jobs._claim(channel="translation")["id"] == layout["id"]
    assert jobs._claim(channel="text")["id"] == text["id"]
    assert jobs._claim(channel="paragraph")["id"] == paragraph["id"]
    assert jobs._claim(channel="translation") is None


def test_babeldoc_codex_bridge_is_local_authenticated_and_keeps_owner_context(paper, monkeypatch):
    seen = []
    def model(system, source, cfg, **kwargs):
        seen.append((auth.current_user_id(), system, source))
        assert kwargs["cancelled"]() is False
        return "Translated paragraph."
    monkeypatch.setattr(providers, "complete", model)
    with auth.as_user("worker-owner"):
        with providers.translation_bridge({"provider": "codex", "model": "gpt-6-luna"}, lambda: auth.current_user_id() != "worker-owner") as cfg:
            assert cfg["base_url"].startswith("http://127.0.0.1:")
            payload = {"messages": [{"role": "system", "content": "Translate only."}, {"role": "user", "content": "Paragraph."}]}
            response = httpx.post(cfg["base_url"] + "/chat/completions", json=payload, trust_env=False)
            assert response.status_code == 403
            response = httpx.post(cfg["base_url"] + "/chat/completions", json=payload, headers={"Authorization": "Bearer " + cfg["api_key"]}, trust_env=False)
            assert response.status_code == 200
            assert response.json()["choices"][0]["message"]["content"] == "Translated paragraph."
    assert seen == [("worker-owner", "Translate only.", "Paragraph.")]


def test_sharing_generated_notes_requires_unchanged_shared_sources(paper):
    from app.core_routes import create_annotation, AnnotationInput
    note = create_annotation(paper["id"], AnnotationInput(**{
        "project_id": "inbox", "side": "original", "page": 1,
        "quote": "Original research", "note": "A private insight.", "color": "yellow",
        "rects": [{"x": .1, "y": .1, "width": .2, "height": .03}], "source_sha": paper["sha256"],
    }))
    job = jobs.enqueue(paper["id"], "inbox", "notes-summary")
    assert not job["can_share"]
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        jobs.set_visibility(job["id"], "shared")
    assert error.value.status_code == 409
    with core.db() as con:
        con.execute("UPDATE annotations SET visibility='shared' WHERE id=?", (note["id"],))
    assert jobs.set_visibility(job["id"], "shared")["visibility"] == "shared"
    with core.db() as con:
        con.execute("UPDATE annotations SET note='A changed public insight.' WHERE id=?", (note["id"],))
    assert jobs._shared_sources_valid(jobs._row(job["id"])) is False


def test_import_parse_artifact_keeps_structure_version_and_reuses(paper, monkeypatch):
    imports.init_imports()
    imports._STOP.clear()
    calls = []
    monkeypatch.setattr(ai, "translation_executable", lambda: "/opt/babeldoc")
    monkeypatch.setattr(parsing, "_babeldoc", lambda paper, exe, cancel: calls.append(paper["sha256"]) or parsing._native(paper, cancel))
    request = imports.create_import(project_id="inbox", paper_id=paper["id"], processing={"parse": True})
    for _ in range(6):
        if not imports.run_once():
            break
    assert imports.get_import(request["id"])["status"] == "succeeded"
    persisted = json.loads(parsing._cache_path(paper["sha256"]).read_text())
    assert persisted["version"] == parsing.VERSION
    assert persisted["parser_version"] == imports.PARSER_VERSION
    assert parsing.ensure_parsed(paper)["provider"] == "babeldoc"
    assert reading._extract(paper, 1)[0]["text"].startswith("Original research")
    assert len(calls) == 1


def test_legacy_parse_cache_is_reused_without_babeldoc(paper, monkeypatch):
    legacy_text = "Original research paragraph with evidence, methods, datasets and limitations."
    cache = {"source_sha": paper["sha256"], "parser_version": "pymupdf-text-v1",
             "pages": [{"page": 1, "text": legacy_text}], "fulltext": "[PDF 第 1 页]\n" + legacy_text,
             "coverage": {"total_pages": 1, "text_pages": 1, "empty_text_pages": [], "visual_evidence_verified": False}}
    path = parsing._cache_path(paper["sha256"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache))
    original = path.read_bytes()
    monkeypatch.setattr(parsing, "_babeldoc", lambda *args: pytest.fail("existing parsing result must be reused"))
    monkeypatch.setattr(ai, "translation_executable", lambda: "/opt/babeldoc")
    result = parsing.ensure_parsed(paper)
    assert result["provider"] == "legacy-parser"
    assert result["fulltext"] == cache["fulltext"]
    block = reading._extract(paper, 1)[0]
    assert block["text"] == legacy_text and block["rects"] == []
    assert path.read_bytes() == original
    cache["source_sha"] = "0" * 64
    path.write_text(json.dumps(cache))
    assert parsing.load_cached(paper["sha256"]) is None


def test_parse_namespaces_separate_reference_contexts(paper, monkeypatch):
    monkeypatch.setattr(ai, "translation_executable", lambda: None)
    one = {**paper, "_parse_cache_namespace": "reference:owner1:project1"}
    two = {**paper, "_parse_cache_namespace": "reference:owner2:project2"}
    assert parsing.ensure_parsed(one)["source_sha"] == paper["sha256"]
    assert parsing.load_cached(paper["sha256"]) is None
    assert parsing.load_cached(paper["sha256"], two["_parse_cache_namespace"]) is None
    assert parsing.load_cached(paper["sha256"], one["_parse_cache_namespace"])
    assert reading._extract(one, 1)[0]["text"].startswith("Original research")


@pytest.mark.parametrize("lines,font", [
    (["本文档由 funstory.ai 的开源 PDF 翻译库", "BabelDOC 38d3896 (https://github.com/funstory-ai/BabelDOC) 翻译，", "本仓库正在积极的建设当中，欢迎 star 和关注。"], "china-s"),
    (["This document was translated by funstory.ai using", "BabelDOC 38d3896 (https://github.com/funstory-ai/BabelDOC).", "The repository is actively developed, welcome to star and follow."], "helv"),
])
def test_promotional_watermark_across_pdf_blocks_preserves_research(lines, font):
    with fitz.open() as document:
        page = document.new_page(width=800, height=700)
        paper_text = "Research compares BabelDOC and other document parsers."
        page.insert_text((40, 80), paper_text)
        for index, line in enumerate(lines):
            page.insert_text((40, 300 + index * 35), line, fontname=font, fontsize=10)
        page.insert_text((40, 470), "Experimental evidence remains visible.")
        before = page.get_text("text")
        assert "funstory" in before and len(page.get_text("blocks")) >= 4
        assert redact_page(page)
        after = page.get_text("text")
        assert "funstory" not in after and "38d3896" not in after
        assert paper_text in after and "Experimental evidence remains visible." in after


def test_corrupt_job_fails_without_stopping_next_job(paper, monkeypatch):
    corrupt = jobs.enqueue(paper["id"], "inbox", "brief")
    with core.db() as con:
        con.execute("UPDATE jobs SET payload='not json' WHERE id=?", (corrupt["id"],))
    assert jobs.run_one(channel="text")
    assert jobs.get_job(corrupt["id"])["status"] == "failed"
    next_job = jobs.enqueue(paper["id"], "inbox", "guide")
    monkeypatch.setattr(ai, "run_text", lambda *args: ("A supported explanation.", {}))
    assert jobs.run_one(channel="text")
    assert jobs.get_job(next_job["id"])["status"] == "succeeded"


def test_worker_loop_survives_transient_claim_error(monkeypatch, caplog):
    calls = []
    def run(**kwargs):
        calls.append(kwargs["channel"])
        if len(calls) == 1:
            raise RuntimeError("transient database failure")
        jobs._STOP.set()
        return True
    monkeypatch.setattr(jobs, "run_one", run)
    jobs._STOP.clear()
    try:
        jobs._loop("text")
    finally:
        jobs._STOP.clear()
    assert calls == ["text", "text"]
    assert "Background text worker iteration failed" in caplog.text
