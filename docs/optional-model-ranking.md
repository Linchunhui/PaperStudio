# Optional model-assisted ranking

Ordinary recommendation GET requests remain deterministic and model-free. The optional
`POST /api/recommendations/rerank` endpoint is disabled unless the deployment explicitly
sets `PAPERSTUDIO_ENABLE_LLM_RERANK=1`.

It reuses the administrator's configured OpenAI-compatible, local-model or Codex provider.
It does not require a separate ranking service or a repository-external agent runtime.

An authenticated client sends the usual `X-CSRF-Token` and, when available,
`X-Reader-Account` headers, plus:

```json
{
  "candidate_ids": ["catalog-id-1", "catalog-id-2"],
  "interests": "Efficient document understanding",
  "confirm_model_call": true
}
```

Before offering this explicit action, the client must explain that the selected public
paper titles/abstracts and the entered interests will be sent to the configured model,
which may incur provider charges. Never call it during page loading, refreshing or
ordinary pagination. No reading history, private notes or saved profile is loaded.
A client should disable repeated clicks while pending and discard results if its
candidate batch or active reader changes before the response returns.

The endpoint accepts 2–50 unique existing public catalog IDs and at most 1,000 interest
characters. Title/abstract excerpts are bounded to 500/3,000 characters per paper;
provider input limits still apply and cause a safe fallback rather than an unbounded call.
The response returns only an ordering of those exact IDs. Invented IDs, duplicates,
missing candidates, malformed JSON, provider errors and concurrent ranking attempts
preserve the submitted order. Calls are capped at 60 seconds and 2,048 output tokens.
No automatic retry is made; a failed request may still have incurred provider charges.

This does not change recommendation cursors, exposure records, user preferences or the
normal feed. Callers apply an accepted ranking only to the selected batch. Disabling the
feature leaves the rest of PaperStudio unaffected. API authentication, CSRF validation
and reader identity checks remain required even when mounted independently.
