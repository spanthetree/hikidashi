# Architecture decision records

Why Hikidashi is built the way it is: one short record per decision that was
argued out in an issue, so the reasoning lives next to the code rather than in
closed threads. [Architecture](../architecture.md) ·
[Operation](../operation.md) · [Back to the README](../../README.md)

Each record has the same parts: **Context** (what forced a choice),
**Decision**, **Alternatives** (what was weighed, and why it lost), and
**Consequences** (what we live with). A record is not edited to say something
new: a later decision gets its own record and marks the old one
**Superseded by**.

| # | Decision | Status | Issue |
|---|---|---|---|
| [0001](0001-sqlite-not-postgres.md) | SQLite, not Postgres | Accepted | #35 |
| [0002](0002-workers-write-through-a-job-source.md) | Workers write through a job source, not raw SQL | Accepted | #72, #35 |
| [0003](0003-embedding-is-server-only.md) | Embedding is server-only | Accepted | #86 |
| [0004](0004-stages-placed-by-local-stages.md) | Stages placed by `HIKIDASHI_LOCAL_STAGES`, not GPU detection | Accepted | #72, #86 |
| [0005](0005-local-accounts-no-instagram-login.md) | Local accounts, no Instagram login | Accepted | #12 |
| [0006](0006-one-primary-tag-per-reel.md) | One primary tag per reel | Accepted; its precedence superseded by 0014 | #21 |
| [0007](0007-trigram-rejected-for-japanese.md) | Trigram rejected for Japanese | Accepted | #33 |
| [0008](0008-mutmut-dropped.md) | Mutation testing (mutmut) dropped for now | Accepted | #39, #70 |
| [0009](0009-local-models-only.md) | Local models only | Accepted | #2, #3, #4 |
| [0010](0010-always-on-workers-before-remote.md) | Always-on workers before remote ones | Accepted | #72 |
| [0011](0011-hybrid-search-rank-fusion.md) | Hybrid search by rank fusion, no reranker | Accepted | #4, #20 |
| [0012](0012-nas-installs-templates-and-yaml.md) | NAS installs: Unraid templates and TrueNAS YAML, any user, no PUID | Accepted; its `HIKIDASHI_WORKER_TOKEN` note superseded by 0013, its TrueNAS pin by 0017 | #87 |
| [0013](0013-standard-api-and-worker-tokens.md) | Standard tokens: user-scoped API tokens, admin-only worker tokens | Accepted | #103 |
| [0014](0014-game-titles-from-evidence.md) | A reel's video game, decided from evidence | Accepted; supersedes 0006's precedence | #117 |
| [0015](0015-filters-intersect.md) | Filters intersect | Accepted | #117 |
| [0016](0016-polyform-noncommercial.md) | Source-available under PolyForm Noncommercial | Accepted | #122 |
| [0017](0017-version-written-once.md) | The version is written once, in pyproject.toml | Accepted; supersedes 0012's TrueNAS pin | #123 |

## Adding one

Copy the shape of any record above, take the next number, and add a row here.
A pull request that changes a boundary, a stage or the schema updates the
matching diagram or record ([CONTRIBUTING](../../CONTRIBUTING.md)).
