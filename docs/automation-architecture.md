# Automated collection architecture

## Operating model

GitHub Actions is the primary scheduler and execution environment. A weekday
source-discovery workflow runs twice after the NSE cash-market session (17:23
and 20:23 India time) and can also be started manually.

The first workflow is intentionally a **source discovery and availability
probe**. It checks official source pages and records candidate download links.
It does not claim that market data has been ingested, and it does not guess
CSV/ZIP schemas from page titles.

## Staged rollout

1. **Source discovery:** verify page reachability, discover actual linked files,
   capture page hashes and candidate URLs.
2. **Raw ingestion:** download exact source bytes, validate content type/size,
   hash bytes, and save immutable raw artifacts with retrieval metadata.
3. **Schema adapters:** parse only after inspecting real source files; reject
   unexpected schema changes and quarantine invalid artifacts.
4. **Normalization:** create typed tables with exchange trade date, source
   publication time, retrieval time, provisional/final state and parser version.
5. **Research:** run independent short-term and positional scoring and produce
   reproducible reports.
6. **Persistence:** retain raw and normalized history in durable object storage.

## Storage policy

- Git is for source code, configuration, tests and documentation—not daily
  bhavcopy archives or large market-data histories.
- Workflow artifacts currently retain only short-lived source-discovery
  diagnostics (14 days). They are not the historical data warehouse.
- Before production ingestion is enabled, configure durable object storage
  (an S3-compatible bucket such as Cloudflare R2 or AWS S3) with credentials
  stored only as GitHub Actions secrets. Credentials must never enter Git,
  logs, workflow artifacts or reports.
- If object storage is not configured, the workflow must fail closed for
  historical ingestion rather than silently discard raw files.

## Reliability and safety

- Scheduled workflows run only from the default branch. GitHub notes that
  scheduled starts can be delayed during periods of high load, and scheduled
  workflows in public repositories can be disabled after 60 days without
  repository activity. Keep manual dispatch available and monitor workflow
  runs. See
  https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- A page response is not proof that its underlying report is available.
- HTTP errors, anti-bot responses, empty downloads and HTML masquerading as
  CSV/ZIP are recorded as failures, never as zero market activity.
- Retry transient failures with bounded backoff; do not hammer exchange sites.
- Do not bypass access controls, CAPTCHAs or terms of service.
- Every downloaded artifact must have a SHA-256 digest and source provenance.
- Participant-category derivatives aggregates must never be attributed to
  individual stocks.

## Local PC fallback

The same Python package and CLI will be runnable locally. The PC fallback will
reuse the same source registry, validators and storage contract; it must not
create a separate interpretation of the data. A local run can backfill a
missing session and upload the resulting immutable artifacts to the same
configured object store.


## Concurrent data-workflow commits

Independent NSE collectors can finish at nearly the same time and update the same
`main` branch. A successful local rebase does not eliminate the small race
between the final fetch and push. All data-writing workflows therefore use
`scripts/retry_git_push.sh`: it fetches the latest branch, rebases the workflow
commit, and retries a rejected push with bounded backoff. It never force-pushes
or discards a conflicting change; a genuine rebase conflict fails for review.
