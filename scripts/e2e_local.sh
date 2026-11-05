#!/bin/bash
# End-to-end test of one validation job, without GitHub or GHCR.
#
# Builds the dandi-validate image locally with Docker, converts it to a SIF with Apptainer, creates
# a Dandiset repository from the template in a local bare "remote", and runs the job exactly as
# the Validate workflow does (`dandi-validations run ... --push origin`). Then it checks that the
# capsule succeeded, that every asset was streamed, and that a second run skips.
#
# Needs: docker, apptainer, and this package installed (`pip install .`), which brings datalad,
# datalad-container, git-annex and con-duct.
#
# Usage: scripts/e2e_local.sh [DANDISET_ID] [WORK_DIRECTORY]

set -euo pipefail

DANDISET_ID="${1:-000006}"
WORK_DIRECTORY="$(realpath -m "${2:-$(mktemp -d -t dandi-validations-e2e-XXXXXX)}")"
REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PINS="${REPOSITORY_ROOT}/src/dandi_validations/pipeline.json"

read_pin() {
    python -c 'import functools, json, sys; print(functools.reduce(dict.get, sys.argv[2].split("."), json.load(open(sys.argv[1]))))' "$PINS" "$1"
}
IMAGE="$(read_pin container.image)"
VERSION="$(read_pin container.version)"
NAME="$(read_pin container.name)"
DANDI_CLI_REPOSITORY="$(read_pin dandi_cli.repository)"
DANDI_CLI_COMMIT="$(read_pin dandi_cli.commit)"

mkdir -p "$WORK_DIRECTORY"
echo "Working in ${WORK_DIRECTORY}"

export DANDI_VALIDATIONS_IMAGE_CACHE="${WORK_DIRECTORY}/images"
mkdir -p "$DANDI_VALIDATIONS_IMAGE_CACHE"
SIF="${DANDI_VALIDATIONS_IMAGE_CACHE}/${NAME}_${VERSION}.sif"
if [ ! -s "$SIF" ]; then
    docker build \
        --build-arg DANDI_CLI_REPOSITORY="$DANDI_CLI_REPOSITORY" \
        --build-arg DANDI_CLI_COMMIT="$DANDI_CLI_COMMIT" \
        --build-arg CONTAINER_VERSION="$VERSION" \
        -t "${IMAGE}:${VERSION}" \
        "${REPOSITORY_ROOT}/containers/dandi-validate"
    apptainer build --force "$SIF" "docker-daemon://${IMAGE}:${VERSION}"
fi

REMOTE="${WORK_DIRECTORY}/remotes/${DANDISET_ID}.git"
CLONE="${WORK_DIRECTORY}/runner/${DANDISET_ID}"
rm -rf "$REMOTE" "$CLONE" "${WORK_DIRECTORY}/sync"
mkdir -p "$(dirname "$REMOTE")" "${WORK_DIRECTORY}/sync"
git init --quiet --bare -b main "$REMOTE"
python - "$DANDISET_ID" "$REMOTE" "${WORK_DIRECTORY}/sync" <<'EOF'
import pathlib, sys
from dandi_validations._repositories import sync_repository

dandiset_id, remote, work = sys.argv[1:]
sync_repository(dandiset_id=dandiset_id, remote_url=f"file://{remote}", codebase_ref="main", work_directory=pathlib.Path(work))
EOF
git clone --quiet "$REMOTE" "$CLONE"

cd "$CLONE"
dandi-validations run --dandiset-id "$DANDISET_ID" --push origin --time-limit-minutes 60

PIPELINE_DIRECTORY="derivatives/dandisets-${DANDISET_ID:0:3}/dandiset-${DANDISET_ID}/pipeline-dandi+validate"
python - "$PIPELINE_DIRECTORY" <<'EOF'
import csv, json, pathlib, sys

pipeline_directory = pathlib.Path(sys.argv[1])
rows = list(csv.DictReader((pipeline_directory / "jobs.tsv").open(), delimiter="\t"))
assert len(rows) == 1 and rows[0]["status"] == "successful", rows
capsule = pipeline_directory / rows[0]["job_id"]
summary = json.loads((capsule / "derivatives" / "summary.json").read_text())
assets = int(rows[0]["assets"])
assert summary["files_streamed"] == assets, (summary["files_streamed"], assets)
assert summary["files_missing_content"] == 0, summary
for name in ("duct_info.json", "duct_usage.jsonl", "container_versions.json", "run.json", "submit.log"):
    assert (capsule / "logs" / name).exists(), name
print(json.dumps({key: summary[key] for key in ("total_results", "by_severity", "by_validator")}, indent=2))
EOF

git log --format='%s' | grep -q '^\[DATALAD RUNCMD\]'
git -C "$REMOTE" log --oneline main | head -n 5

# A second run finds the successful capsule and does nothing.
dandi-validations run --dandiset-id "$DANDISET_ID" --push origin | grep -q "already validated"
echo "End-to-end test passed."
