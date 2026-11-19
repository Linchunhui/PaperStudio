# Project-code synchronization

This update builds on PaperStudio 0.5.0. Existing onboarding, model settings,
project templates, bibliography import, portability, demo and container workflows
remain available.

## Included

- Compact search/recommendation cards with two deduplicated reasons, while keeping
  full insights, source details, project actions and feedback accessible.
- Next-batch replacement and append pagination without silently looping after the
  candidate list is exhausted; failures preserve the displayed list.
- Optional numbered-figure previews from already imported, explicitly catalog-linked
  PDF files that the current reader may access. No download or model call occurs.
  Embedded raster figures in the first five unrotated pages are supported; vector-only,
  scanned and unnumbered figures can fall back to text-only cards. Selection is a
  deterministic candidate, not a claim that every paper's main figure was identified.
- Ctrl/Cmd + wheel zoom confined to the PDF area, preserving the pointer position.
- Managed reading spaces with separate inboxes, profiles and activity. Switching
  rotates CSRF credentials, reloads the workspace and rejects stale-tab writes.
  These are spaces controlled by the same login owner, not independent credentials.
- Provider-neutral topic packages with source hashes, evidence spans, review and
  immutable publication history. No research content or review history is bundled.
  See `config/topics/README.md`.
- Optional explicit model reranking through the existing OpenAI-compatible/local/
  Codex provider. It is off by default and never runs on a feed GET. See
  `docs/optional-model-ranking.md` for the authenticated API and disclosure contract.

## Deliberately excluded

No VPF transport, KEV endpoint, private corpus adapters, machine-specific paths,
credentials, real user records, PDFs, vector snapshots or database files are added.
The existing LLM/provider APIs remain the model integration boundary. The local
multi-stage understanding runtime is not copied because it requires an external
VPF workflow; existing paper-guide/brief APIs remain intact. PDF font rewriting
and failed-page translation repair tied to that pipeline are not included.

New account/activity/topic tables are initialized by the application, but no
existing deployment database is imported or replaced. Test data is synthetic.
Do not interpret passing schema/function tests as validation of generated research.

## Validation

Run `bash scripts/check.sh` for Python, JavaScript syntax and frontend regressions.
Run `python scripts/browser_smoke.py` with `requirements-browser.txt` installed for
real Chromium setup, managed-reader switching, anchored notes, templates, exports
and narrow-screen checks. Tests do not call a live model. Container builds and
real external model quality require their separate acceptance steps.

## November 19 unified list update

Index, personal library, project and starred lists now share the compact card
renderer, with one/two-column controls and a collapsed structured interpretation.
Library enrichment is opt-in (`include_card=true`); verified catalog mapping adds
public metadata without replacing local IDs, custom titles, project membership,
reading progress or access checks. Opening a card interpretation renders existing
public `catalog-material` fields only: it does not fetch a private corpus, download
a PDF or start a model job, and is not a full-paper understanding certificate.
Missing metadata and interpretations stay explicitly unavailable. Project actions
remain in a collapsed management area. No personal library contents or index data
are bundled. The private bulk-corpus screen and its remote adapters are not copied.
