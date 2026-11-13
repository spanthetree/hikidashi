# 0021. The server asks a model while someone waits, and recipes are translated as they are read

- **Status:** Accepted; narrows [#86](https://github.com/spanthetree/hikidashi/issues/86)'s rule that the server never calls Ollama
- **Date:** 2026-11-13
- **Issue:** #45 (ask your library), #114 (a recipe in your language)

## Context

Every model call so far was a pipeline stage: a worker claims reels and runs
them in the background, so the server never called Ollama itself (#86). Many
servers have no model of their own: an always-on NAS whose worker, and
Ollama, are on a GPU desktop that is often off.

Two new things want a model. A recipe in the reader's language can be made
ahead of time. An answer to a question about the library can't: someone is
waiting for it.

## Decision

**Translation is part of the recipe stage.** Whatever reads a recipe, a
worker on Ollama or the hosted runner on an API, translates it into the
app's languages on the same model, and the result travels with the recipe
(also over the remote protocol). The server only serves it from
`recipe_translation`. A recipe with no translation yet, one someone corrected
say, is translated on the spot only with that person's hosted API or a
self-hosted provider; otherwise it is shown as written until the next pass.

**Ask goes to a model directly,** in this order: the person's hosted API when
their reels use one (under its budget, counted like the stages); a
self-hosted provider (Ollama of your own, #125's settings) set for the recipe
stage, theirs or the server's, **whether or not the hosted switch is on**,
since nothing leaves the network; and last the Ollama at the server's
`OLLAMA_URL`.

## Consequences

- A server with no model still shows translated recipes, made by the desktop
  worker while it was on.
- Every recipe is read once more (`RECIPE_V` 6) to translate the ones there are.
- Ask needs one of the three. With none, it says there is no model, and
  nothing else changes.
- The hosted switch still decides the pipeline: "off sends nothing" holds
  for providers off your network.
