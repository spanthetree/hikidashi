# 0004. Stages placed by `HIKIDASHI_LOCAL_STAGES`, not GPU detection

- **Status:** Accepted
- **Date:** 2026-10-25, defaults revised 2026-10-27 (#86)
- **Issue:** #72, #86

## Context

With always-on workers (#72), each host needs to know which stages to run. The
first idea was to detect a GPU and run the heavy stages only where there is
one.

## Decision

The operator says which stages a worker runs: `HIKIDASHI_LOCAL_STAGES` is
`all`, `none` or a list such as `asr,ocr`, and `--stages` overrides it for one
run. Each Docker image sets a default matching the tools it has (server:
`enrich,embed`; worker: `asr,ocr,tag,recipe`). Under uv the default is `all`.
Nothing is detected.

## Alternatives

- **GPU detection.** The `cpu` profile works on any machine, only slowly, and an
  always-on NAS transcribing on CPU while the desktop is off is a layout people
  want. Detection already chooses the *profile* (`HIKIDASHI_PROFILE=auto`:
  which model, which precision); also letting it choose *whether* to run would
  hide a decision that belongs to the operator.

## Consequences

- Readiness doesn't care where a stage runs, so any layout works on one queue.
  A stage nobody runs leaves its reels waiting, shown on the Pipeline page.
- A stage listed for an image without its tools (like `embed` on the worker) is
  reported as unable to run, never failed, and its reels are left for a worker
  that can.
- The layouts are documented rather than inferred
  ([architecture.md](../architecture.md#deployment-topologies)).
