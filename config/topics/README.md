# Versioned topic packages

The topic reader is independent of model providers. It does not call a model or
load a private corpus. An administrator reviews each proposed update and then
publishes a new immutable version. Reading older versions remains supported.

Install operator-reviewed source packages under `config/topics/<slug>/`, then
restart the application. Slugs use lowercase letters, digits and hyphens.
The topic index is empty when no packages are installed. Private Mac research
assets and existing reviewer identities are intentionally not shipped.

Each package contains a UTF-8 baseline text, source snapshots and `manifest.json`:

```json
{
  "title": "Your topic",
  "description": "Research questions and method evolution",
  "baseline": "baseline.txt",
  "baseline_sha256": "<64-character SHA256 of baseline bytes>",
  "versions": [
    {"version": 1},
    {
      "version": 2,
      "parent": 1,
      "changes": [{
        "id": "new-method",
        "title": "A new method",
        "paragraphs": ["Explain its contribution in your own words."],
        "practice": "An actionable takeaway.",
        "evidence": [{
          "source_url": "https://example.org/paper",
          "document_version_id": "paper-v1",
          "locator": "Section 3",
          "snapshot": "source.txt",
          "source_sha256": "<64-character SHA256 of snapshot bytes>",
          "start": 0,
          "end": 5,
          "quote": "Exact"
        }]
      }]
    }
  ]
}
```

The URLs and text above are placeholders, not research evidence. Use only material
you are authorized to distribute. Source snapshot hashes bind original UTF-8
bytes; `start`/`end` are Python Unicode character offsets into the decoded text,
not byte offsets. The exact substring must match the quote. URLs must be HTTPS.

Version 1 is an explicitly installed baseline and is immediately readable. Later
versions are drafts until every update is reviewed and at least one is approved.
Only approved changes are shown, including approved changes along the parent
chain. Rejected changes remain in the review history. Drafts cannot be viewed by
readers, and only administrators may access review or publish actions. Repeated
reviews/publications are rejected. A stale parent cannot be published.

To extend a topic, append a version to the manifest with the current published
parent; preserve existing baseline bytes and version definitions. Do not include
`review` records in packages. Reviews are created by authenticated users and
stored with the application's normal state, never as source control fixtures.
Changing an imported version fails validation instead of rewriting history.

Routes:
- `/topics`: installed topics
- `/topics/<slug>` and `?version=N`: current/historical published reading
- `/topics/<slug>/review`: administrator review and release
- `POST /api/admin/topics/<slug>/review`: version, change_id, decision, note
- `POST /api/admin/topics/<slug>/publish`: version

All routes require login; mutations also require the normal CSRF token. Baseline
and update text are escaped, not executed as HTML. Review pages use the shared
topic stylesheet and a same-origin script. Source hashes verify integrity and
quote alignment, not the scientific correctness of a claim.
