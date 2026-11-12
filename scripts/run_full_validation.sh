#!/usr/bin/env bash
# Execute from the repository root on macOS or Linux.
set -Eeuo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
LOG_DIR="${LOG_DIR:-test-results}"
mkdir -p "$LOG_DIR"
LOG_DIR="$(cd "$LOG_DIR" && pwd)"
echo "Results: $LOG_DIR"

"$PYTHON" --version | tee "$LOG_DIR/python-version.txt"
"$PYTHON" -m compileall -q . 2>&1 | tee "$LOG_DIR/compile.log"
"$PYTHON" -m unittest discover -v 2>&1 | tee "$LOG_DIR/unittest.log"
"$PYTHON" evaluation.py 2>&1 | tee "$LOG_DIR/evaluation.log"
"$PYTHON" scripts/evaluate_answers.py fixtures/response_quality_examples.json 2>&1 | tee "$LOG_DIR/snapshots.log"
"$PYTHON" legal_evidence.py 2>&1 | tee "$LOG_DIR/evidence.log"
"$PYTHON" scripts/validate_legal_data.py 2>&1 | tee "$LOG_DIR/legal-data.log"
"$PYTHON" scripts/check_copyright_hygiene.py 2>&1 | tee "$LOG_DIR/copyright.log"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    IMAGE="local-research-agent:test"
    docker build -t "$IMAGE" . 2>&1 | tee "$LOG_DIR/docker-build.log"
    docker run --rm --entrypoint python "$IMAGE" -c "import api, agent; print('Container imports OK')" 2>&1 | tee "$LOG_DIR/docker-imports.log"
else
    echo "Docker not available; Docker checks skipped" | tee "$LOG_DIR/docker-skipped.txt"
fi
echo "All executed checks passed; inspect Docker skip notice if present."
