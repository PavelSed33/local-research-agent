# CI evidence checkpoint (2026-11-14)

This document is an audit checkpoint, **not** a release approval. The branch is
not ready for commercial deployment without a successful run on its current HEAD.

The last inspected historical run, [43500624142](https://github.com/PavelSed33/local-research-agent/actions/runs/43500624142),
reported failures on Python 3.10, 3.11 and 3.12. The failures included
`archive_file_unavailable` in archive, case-report, workspace backup and HTTP
integration tests, and missing statutory text extracts in
`scripts/validate_legal_data.py`. Docker and copyright hygiene passed on
that historical run only. This does not establish their status on this commit.

The validation gate must remain strict. Do not recreate deleted third-party
licensed PDFs, insert synthetic legal extracts as real law, disable failing
assertions, or infer legal acceptance from technical smoke tests.

## Evidence gates (no credit without a passing artifact)

- Python 3.10, 3.11, 3.12: full unittest matrix passing on current HEAD.
- Security: authentication, source admission, copyright hygiene and negative tests.
- Docker: build, packaging policy and API smoke.
- HTTP: authenticated and unauthenticated requests, archive/workspace endpoints.
- Archive: genuine permitted fixtures, identity, integrity and idempotence.
- Legal sources: authoritative publication, edition date, completeness, licensing.
- OCR: real scan accuracy and error handling.
- Backup: create, restore, corruption detection and access revocation.
- Legal acceptance: reviewed gold-standard scenarios and approved outcomes.

Each gate requires evidence. A passing CI matrix alone is insufficient for
90% readiness because legal acceptance and external source provenance are
separate release blockers.
