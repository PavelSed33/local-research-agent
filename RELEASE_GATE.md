# Release gate: legal research agent

The following requirements are **blocking** for a public/commercial launch. A passing Docker smoke test alone is not a release approval.

## P0 gates
- [x] Docker image builds, starts, and passes API smoke check (GitHub Actions run 43494727543).
- [ ] Entire supported Python test suite passes on 3.10, 3.11, 3.12. Do not silently skip failures.
- [ ] Tests relying on removed legal source archives are rewritten with synthetic fixtures or clearly separated into a *non-passing* external-data acceptance suite. Never restore licensed extracts.
- [ ] Each statute used in answers has independently confirmed official source, version, effective date, content hash, and review decision. The 59-item candidate registry is **not** a law corpus.
- [ ] Legal expert acceptance against representative real-world tasks, including wrong citations, obsolete provisions, and missed material risks.
- [ ] Multi-user authentication and per-tenant isolation are verified, including negative access tests, rate limits, and secret management.
- [ ] Backups, restore drills, and sensitive-data retention/deletion policy tested.
- [ ] Production integrations (LLM, search, official legal source, tax authority) validated under failure and timeout conditions.

## P1 gates
- [ ] Court decision provenance, appeal chain and outcome checks.
- [ ] PDF/OCR ingestion and multi-document case folder acceptance.
- [ ] Legal edition change detection and re-indexing.
- [ ] Load, concurrency, observability and incident response exercises.

## Release decision
Current decision: **NO-GO for public commercial launch**.
A restricted supervised pilot may be considered after its scoped workflows pass their own legal and security acceptance.

Never interpret a successful CI job as proof of legal accuracy or current legal coverage.
