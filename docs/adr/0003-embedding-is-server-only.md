# 0003. Embedding is server-only

- **Status:** Accepted
- **Date:** 2026-10-27
- **Issue:** #86

## Context

When the Docker image was split in two (#86), each stage had to go to one image
or the other. Embedding is an ML stage like ASR, so the worker image looked
like the natural home, and the first proposal put it there.

## Decision

`embed` runs only on the server: in the server image, as the `server-worker`
container, and never on a remote worker (`protocol.REMOTE` leaves it out).

## Alternatives

- **Embed on workers, checked by model name and `EMBED_V`.** A vector is only
  comparable with vectors from the same model, and search embeds every query on
  the server. A worker with another model, another quantisation or another
  library version would write vectors that are quietly wrong: search would
  return the wrong reels, with no error anywhere. A name check can't catch every
  way two installs differ.
- **Embed queries on a worker too.** That would put a remote machine in the
  path of every search.

## Consequences

- One model writes every vector and embeds every query, so they are always
  comparable.
- The server needs the embedder (fastembed, about 1 GB loaded) even with no
  other stage. Measured at about 835 MB for the whole server, and cheap on CPU.
- A server whose transcription and tagging wait for a desktop would leave new
  reels out of semantic search, so the embedder goes ahead from captions while
  no worker can run `asr`, `ocr` or `tag`, and re-embeds by digest when they
  arrive ([operation.md](../operation.md#the-stage-graph)).
