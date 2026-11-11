# 0016. Source-available under PolyForm Noncommercial

- **Status:** Accepted
- **Date:** 2026-11-11
- **Issue:** #122

## Context

The repo had no license, which means nobody may use, copy or fork it. It needs
one before it goes public. The aims are: anyone may use, copy, change and share
Hikidashi for free; nobody else may sell it, repackage it, or build it into
something they sell; and a hosted version run by the maintainer stays possible
later.

## Decision

Hikidashi is licensed under the
[PolyForm Noncommercial License 1.0.0](../../LICENSE) (SPDX
`PolyForm-Noncommercial-1.0.0`). Personal use, research, hobby use and use by
charities, schools and public bodies are allowed; commercial use is not. The
maintainer holds the copyright, so commercial terms, for a hosted version or
for a company that asks, can be offered separately.

Contributions come in under Apache-2.0 to the maintainer, as well as under the
project license to everyone ([CONTRIBUTING](../../CONTRIBUTING.md#licensing-your-contribution)).
Without that, a contributor's code would reach the maintainer under the
noncommercial terms too, and a hosted version could not include it.

## Alternatives

- **AGPL-3.0.** Open source, and hosted forks must publish their changes. It
  still lets anyone sell Hikidashi or run it as a paid service, which is the
  thing to rule out.
- **MIT or Apache-2.0.** As permissive as possible, so nothing stops resale.
- **PolyForm Shield.** Allows any use except competing with the maintainer.
  A company could still build Hikidashi into a paid product that doesn't
  compete, which is broader than wanted.
- **Elastic License 2.0.** Rules out offering it as a hosted service, but not
  selling or bundling it.
- **Business Source License 1.1.** Fits for up to four years, then turns into
  an open-source license. More moving parts than a personal project needs.

## Consequences

- Hikidashi is **source-available, not open source**: the license is not
  OSI-approved, and the docs never call it open source. Some package
  directories list only OSI-licensed projects.
- A business can't use it, even internally, without asking for other terms.
- A hosted version needs more than the code: the models must allow it too.
  `qwen2.5:3b` (the CPU tagger) is under the noncommercial Qwen Research
  License, and embeddinggemma under the Gemma terms. [THIRD_PARTY.md](../../THIRD_PARTY.md)
  lists them all.
- The license is only as strong as the will to enforce it. It gives grounds
  to object, not a way to prevent.
