# PaperStudio

**Discover papers. Read with evidence. Build your own research projects.**

[简体中文](README.md) · [Interactive demo](https://linchunhui.github.io/PaperStudio/) · [Releases](https://github.com/Linchunhui/PaperStudio/releases) · [Contribute](CONTRIBUTING.md)

PaperStudio is a self-hosted research workspace for individuals and small groups. Import an arXiv link or PDF, read the original alongside optional translations, anchor notes to passages, and export your research.

**Start without a model.** PDFs, notes, nested projects and exports work independently. AI tasks use your own compatible API, local model service, or optional Codex CLI. The application UI is currently primarily Chinese; this README provides an English getting-started path.

## Try it

The [read-only demo](https://linchunhui.github.io/PaperStudio/) requires no account or key. It includes self-authored documents, prepared translations and a one-minute guided tour. It demonstrates the interface without running a model. The same demo is available at `/demo/` on your server, or as `paperstudio-demo.zip` in Releases.

## Features

- Discover papers through HF, arXiv, an optional web-search provider and a provenance-preserving historical index.
- Organize papers in nested personal projects, track reading progress and share selected projects with a group.
- Read continuously scrolling PDFs or a reflowed view; select text and save source-anchored notes.
- Request paragraph translation, bilingual reflow or progressive page-by-page PDF translation. Translation is off by default.
- Generate briefs, note summaries and evidence-linked answers or comparisons on demand.
- Preview BibTeX, RIS and Zotero CSV imports. Export project notes and references as Markdown, self-contained HTML, JSON and BibTeX.
- Create OCR/document, diffusion language model and VLM project structures from templates.

PDF layout translation requires BabelDOC. AI tasks require a configured model. Full-web search requires a search provider. Templates organize questions and folders; they are not verified literature reviews. Similarity graphs do not claim citation relationships.

## Quick start with Docker

Requires Docker and Compose v2. Published images currently target Linux amd64; build from source for other architectures.

```bash
mkdir paperstudio
cd paperstudio
curl -fL https://github.com/Linchunhui/PaperStudio/releases/download/v0.5.0/compose.release.yaml -o compose.yaml
docker compose up -d
docker compose exec paperstudio cat /data/bootstrap-token
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Use the one-time token to create your administrator account. There is no default password.

1. Select **打开阅读示例** to open the built-in sample.
2. Select text, open the context menu, choose **笔记**, and save a note.
3. Open **我的论文库 → 迁入与导出** for reference imports, templates and exports.
4. When ready, open **Skill 与配置 → 连接阅读模型**. Save the provider configuration, then test connection and an actual call separately.

The default image excludes the large historical dataset and Codex. Load the complete dataset from administrator settings when needed. `0.5.0-full` includes all original data; `0.5.0-codex` also includes the optional CLI. Releases and images are generated only after CI passes. If a release is not yet available, use the source installation below.

## Source installation

Python 3.12 is required. Node.js 22 is used only for development checks.

```bash
git clone --depth 1 https://github.com/Linchunhui/PaperStudio.git
cd PaperStudio
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
./scripts/start.sh --no-demo
```

Read `data/bootstrap-token` in a second terminal. On Windows run `.venv\Scripts\python.exe run.py --no-demo`. To skip the initial full catalogue load, set `PAPERSTUDIO_CATALOG_MODE=light` in `.env`; load it later from administrator settings. Existing catalogue records are never deleted by this setting.

## Models, data and upgrades

The UI supports compatible `/chat/completions` APIs, trusted local services and optional Codex. Keys stay on the server. A connection check is distinct from an actual model call, which may incur a small cost. Within Docker, the host model service can be reached through `host.docker.internal` when its listener permits that connection. Full PDF translation also needs BabelDOC and its resources.

Existing `.env` configuration remains effective until an administrator explicitly saves a UI override. Set `PAPERSTUDIO_MODEL_CONFIG=environment` to lock deployment settings. Credentials in the data directory must be treated as private when backing up.

Stop the service and back up the **entire data directory**, plus environment and optional CLI credentials, before upgrading. Keep the same data volume. A project export is portable reading content, not a full server backup. See [deployment and recovery instructions](docs/deployment.md) (Chinese).

The full historical data pack keeps the original catalogue, relationship and reading-card files with SHA256 verification. Source coverage, uncertainty and third-party rights are documented in [DATA_NOTICE](DATA_NOTICE.md). The app has no default telemetry; explicit AI, search and download actions contact your chosen services.

## Development

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
./scripts/check.sh
```

CI also exercises a real Chromium journey and container startup/restart. It does not certify the quality of every model or BabelDOC installation. Contributions in English or Chinese are welcome: [CONTRIBUTING](CONTRIBUTING.md), [ROADMAP](ROADMAP.md), [SECURITY](SECURITY.md).

Original project code is licensed under **AGPL-3.0-only**. See [LICENSE](LICENSE), [third-party notices](THIRD_PARTY_NOTICES.md) and [data notice](DATA_NOTICE.md).
