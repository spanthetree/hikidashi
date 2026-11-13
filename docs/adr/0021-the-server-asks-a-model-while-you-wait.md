# 0021. The server asks a model while someone waits

- **Status:** Accepted; narrows [#86](https://github.com/spanthetree/hikidashi/issues/86)'s rule that the server never calls Ollama
- **Date:** 2026-11-13
- **Issue:** #114 (a recipe in your language), #45 (ask your library)

## Context

Every model call so far was a pipeline stage: a worker claims reels and runs
them in the background, so the server never called Ollama itself (#86).
Translating a recipe into the reader's language and answering a question about
the library are different: someone is waiting for the answer, it is about
their own library, and there is nothing to queue.

## Decision

**The server asks the model directly for these, following the person's AI
choice (#125):** their own hosted API when their reels use it, the server's
when they follow a hosted system default, and otherwise the local Ollama at
`OLLAMA_URL`, the same one the worker tags with. A hosted API over its monthly
budget is not used; its use is counted with the stages'. On CPU the local
model is slow but works.

- Translations are kept (`recipe_translation`), by a digest of the recipe and
  the language, so each is asked for once.
- Only these two. Tagging, recipes and transcription stay pipeline stages.

## Consequences

- The server container needs `OLLAMA_URL` set to the worker's Ollama; the
  compose files and NAS templates now pass it.
- With no Ollama reachable and no hosted API, the sheet says the recipe was
  not translated, and Ask says there is no model. Nothing else changes.
