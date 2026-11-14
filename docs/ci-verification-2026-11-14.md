# CI verification checkpoint — 2026-11-14

Target branch: `fix/recover-research-engine-20261112`.

This commit requests a fresh push-triggered CI evaluation of the current branch, rather than rerunning an older commit. It does not change application code, test expectations, statutory evidence, or release gates.

## Acceptance evidence required

- Python 3.10, 3.11, 3.12: all unit and regression jobs pass on the same head SHA.
- Security: copyright hygiene, source allowlists, and authentication tests pass.
- Docker and HTTP: container smoke and authenticated/unauthenticated endpoint checks pass.
- Archive, OCR, backup/restore: independent integration checks pass.
- Legal sources: official provenance, edition dates, hashes, and licensing independently verified; quarantined documents remain excluded.
- Legal acceptance: signed expert review and reproducible legal benchmark evidence. Synthetic evaluation alone does not count.

No 90% readiness claim or commercial release is authorized by this checkpoint.
