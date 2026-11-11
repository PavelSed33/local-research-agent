# Offline acceptance: legal corpus guards

Added `tests/test_legal_corpus_safety.py`. Run without external network or GitHub Actions:

```bash
python -m unittest discover -s tests -p 'test_legal_corpus_safety.py' -v
```

Tests cover blocked RusLawOD provenance, mandatory explicit approval and SHA-256 format, half-open edition intervals `[effective_from, effective_to)`, missing and unverified documents. They use temporary SQLite data only.

**Limits:** A passing test does not confirm actual source licensing, completeness, API availability, the correctness of the edition or security of any third-party dependency. The manifest check is not yet enforced inside importers; therefore do not use it as a production security boundary. Offline tests have not been executed in GitHub Actions.
