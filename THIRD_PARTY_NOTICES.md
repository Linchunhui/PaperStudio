# Third-party notices

Copyright (C) 2026 PaperStudio contributors. Original project code is provided under AGPL-3.0-only, without warranty. The complete terms are in LICENSE. Existing notices in third-party files remain in effect.

This file identifies key components; each installed distribution's own license and dependency notices are authoritative. The project license does not replace their terms.

| Component | Use | License / upstream |
| --- | --- | --- |
| PyMuPDF 1.26.6 / MuPDF | PDF reading, text extraction and vector fixtures | [AGPL v3 or Artifex commercial licensing](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright); this project uses the open-source distribution |
| KaTeX 0.19.1 | Browser math rendering | MIT; vendored license at [web/vendor/KaTeX-LICENSE.txt](web/vendor/KaTeX-LICENSE.txt) |
| FastAPI | API framework | MIT; [upstream](https://github.com/fastapi/fastapi) |
| Uvicorn, HTTPX | Server and HTTP client | BSD-3-Clause; [Uvicorn](https://github.com/encode/uvicorn), [HTTPX](https://github.com/encode/httpx) |
| PyYAML | Structured configuration | MIT; [upstream](https://github.com/yaml/pyyaml) |
| NumPy | Numerical data | BSD-3-Clause; [upstream](https://github.com/numpy/numpy) |
| bibtexparser 1.4.3 | Bibliography import/export | Distributed under LGPLv3 or BSD; [upstream license](https://github.com/sciunto-org/python-bibtexparser/blob/v1.4.3/LICENSE) |
| Codex CLI (optional) | Operator-selected model runtime | Apache-2.0; [upstream](https://github.com/openai/codex) |
| BabelDOC (optional) | PDF layout translation | [Upstream license and dependency notices](https://github.com/funstory-ai/BabelDOC); installed separately, models/fonts may have additional terms |
| Playwright (development only) | Chromium regression tests | Apache-2.0; [upstream](https://github.com/microsoft/playwright-python) |

Docker base images, OS packages, fonts and transitive dependencies retain their own notices. The application runs as a non-root user; model credentials and personal data are not embedded in published images.

The reading demo PDFs, prepared Chinese text and example notes are self-authored project fixtures. They are not excerpts from a published research paper. Third-party research metadata and generated reading cards are covered separately by [DATA_NOTICE.md](DATA_NOTICE.md).

For source-derived or vendored additions, retain the upstream license, attribution, version and modification description. If you distribute a modified deployment, make its corresponding source available as required by the applicable license and update the UI source link to that source.
