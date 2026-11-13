#!/bin/bash
# Validate a Dandiset by streaming, following the recipe at the top of dandi/dandi-cli#1956.
#
# This script runs INSIDE the dandi-validation image: submit.sh hands it to
# `datalad containers-run`, which executes it through Apptainer and records the run. It is copied
# into every job capsule's code/ directory, so the capsule holds the exact code that ran.
#
# Usage: run.sh CAPSULE_DIRECTORY DANDISET_ID PARAMETERS_FILE DANDISET_YAML_URL
#
#   1. Mint a DataLad dataset of the Dandiset in the capsule's sourcedata/DANDISET_ID/, from the
#      archive's S3 manifest, sourcedata/assets.jsonld. Each asset is annexed in the capsule by its
#      SHA-256 key, with the URL of its blob on S3, and nothing is downloaded. An asset without
#      both (e.g., a Zarr asset) cannot be streamed, and fails the run.
#   2. Download the Dandiset's dandiset.yaml into the dataset, from DANDISET_YAML_URL, since
#      dandi validate finds the Dandiset's root by it. `datalad containers-run` records the dataset
#      as an output of the run, and the manifest as its input.
#   3. Run `dandi validate --missing-file-content=stream` on it, so that the content-dependent
#      validators (pynwb, nwbinspector) read only what they need, straight from S3.
#   4. Write the results (JSON Lines, as dandi validate writes them) to the capsule's derivatives/
#      directory, with dandi validate's --summary of them: the counts by severity, validator and
#      standard. The summary is also printed to the logs.
#
# `dandi validate` exits 1 whenever it reports an error-level issue, which is a successful
# validation here. It writes --output only once every file has been validated, so the
# presence of that file is what marks a completed run; anything else exits non-zero.

set -euo pipefail

CAPSULE_DIRECTORY="$1"
DANDISET_ID="$2"
PARAMETERS_FILE="$3"
DANDISET_YAML_URL="$4"

SOURCEDATA_DIRECTORY="${CAPSULE_DIRECTORY}/sourcedata"
MANIFEST="${SOURCEDATA_DIRECTORY}/assets.jsonld"
INTERMEDIATE_DIRECTORY="${CAPSULE_DIRECTORY}/intermediate"
OUTPUT_DIRECTORY="${CAPSULE_DIRECTORY}/derivatives"
SOURCE_DATASET="${SOURCEDATA_DIRECTORY}/${DANDISET_ID}"
RESULTS_FILE="${INTERMEDIATE_DIRECTORY}/validation.jsonl"
SUMMARY_FILE="${INTERMEDIATE_DIRECTORY}/validation_summary.txt"

parameter() {
    python -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$PARAMETERS_FILE" "$1"
}
MISSING_FILE_CONTENT="$(parameter missing_file_content)"
JOBS="$(parameter jobs)"
MIN_SEVERITY="$(parameter min_severity)"

export DANDI_NO_ET=1

rm -rf "$INTERMEDIATE_DIRECTORY" "$OUTPUT_DIRECTORY" "$SOURCE_DATASET"
mkdir -p "$INTERMEDIATE_DIRECTORY"

# Apptainer binds the host's home directory, and with it the host's git configuration (signing
# programs, hooks, ...). git and git-annex run here with a configuration of their own.
export GIT_CONFIG_GLOBAL="${INTERMEDIATE_DIRECTORY}/gitconfig"
export GIT_CONFIG_NOSYSTEM=1
git config --global user.name "dandi-validations"
git config --global user.email "dandi-validations@users.noreply.github.com"
# git-annex in the image cannot use the network (it has no /etc/protocols), and it would probe the
# capsule's git remotes for an annex. They hold none: the content is on S3.
for remote in $(git -C "$CAPSULE_DIRECTORY" remote); do
    git config --global "remote.${remote}.annex-ignore" true
done

echo "=== dandi-validation image ==="
python -c \
    'import json, sys; v = json.load(open(sys.argv[1])); print(json.dumps({k: v[k] for k in ("validator_versions", "tool_versions", "git", "git_annex")}, indent=2))' \
    /opt/dandi-validation/versions.json \
    || echo "(no version record in this image)"

# The fields of each asset that `datalad addurls` needs, as it reads them: a flat JSON record.
addurls_records() {
    python - "$MANIFEST" << 'EOF'
import json
import sys

records = []
unsupported = []
for asset in json.load(open(sys.argv[1])):
    sha256 = asset.get("digest", {}).get("dandi:sha2-256")
    blobs = [url for url in asset.get("contentUrl", []) if "amazonaws.com" in url and "/blobs/" in url]
    if sha256 and blobs:
        records.append({"path": asset["path"], "size": str(asset["contentSize"]), "sha256": sha256, "url": blobs[0]})
    else:
        unsupported.append(asset.get("path"))
if unsupported:
    sys.exit(f"{len(unsupported)} assets have no SHA-256 digest or no blob on S3, so they cannot be streamed: {unsupported}")
json.dump(records, sys.stdout)
EOF
}

echo "=== Minting the DataLad dataset of Dandiset ${DANDISET_ID} from its S3 manifest ==="
# Unsaved, so that `datalad containers-run` saves the files in the run's own commit.
addurls_records | datalad addurls -d "$CAPSULE_DIRECTORY" --nosave --exclude-autometa '.*' \
    --key 'et:SHA256-s{size}--{sha256}' \
    - '{url}' "sourcedata/${DANDISET_ID}/{path}"
# addurls only logs a URL it failed to register, and a file without one cannot be streamed.
listed="$(python -c 'import json, sys; print(len(json.load(open(sys.argv[1]))))' "$MANIFEST")"
registered="$(git -C "$SOURCE_DATASET" annex find --in=web | wc -l)"
echo "Registered ${registered} of ${listed} files with their S3 URLs (none downloaded)."
if [ "$registered" -ne "$listed" ]; then
    echo "Not every file got its S3 URL, so the Dandiset cannot be streamed." >&2
    exit 1
fi
# Only dandi validate needs it, to find the Dandiset's root, and it does not read its content.
python -c 'import shutil, sys, urllib.request; shutil.copyfileobj(urllib.request.urlopen(sys.argv[1]), open(sys.argv[2], "wb"))' \
    "$DANDISET_YAML_URL" "${SOURCE_DATASET}/dandiset.yaml"

echo "=== Validating Dandiset ${DANDISET_ID} (--missing-file-content=${MISSING_FILE_CONTENT}, -J ${JOBS}) ==="
validate_status=0
dandi validate \
    --missing-file-content="$MISSING_FILE_CONTENT" \
    --jobs "$JOBS" \
    --min-severity "$MIN_SEVERITY" \
    --format json_lines \
    --output "$RESULTS_FILE" \
    --summary \
    "$SOURCE_DATASET" \
    || validate_status=$?
echo "dandi validate exited with ${validate_status}"

if [ ! -f "$RESULTS_FILE" ] || [ "$validate_status" -gt 1 ]; then
    echo "dandi validate did not complete; no results are recorded." >&2
    exit $(( validate_status == 0 ? 1 : validate_status ))
fi

echo "=== Summarizing results ==="
# Reloaded with --summary, dandi validate prints the summary last, after the results rendered as
# text, of which --max-per-group 0 shows none. Only the summary is kept.
summary_status=0
dandi validate \
    --load "$RESULTS_FILE" \
    --min-severity "$MIN_SEVERITY" \
    --max-per-group 0 \
    --summary \
    > "${INTERMEDIATE_DIRECTORY}/summary_output.txt" \
    || summary_status=$?
if [ "$summary_status" -gt 1 ]; then
    echo "dandi validate could not summarize the results (exit code ${summary_status})." >&2
    exit "$summary_status"
fi
sed -n '/^--- Validation Summary ---$/,$p' "${INTERMEDIATE_DIRECTORY}/summary_output.txt" > "$SUMMARY_FILE"

echo "=== Writing results ==="
# The results are kept in git, uncompressed, so that they can be read on GitHub, which refuses
# any file over 100 MiB. Larger results are not recorded, and the capsule is left failed.
RESULTS_BYTES="$(stat -c %s "$RESULTS_FILE")"
if [ "$RESULTS_BYTES" -ge 104857600 ]; then
    echo "The results (${RESULTS_BYTES} bytes) exceed GitHub's 100 MiB limit for a file; they are not recorded." >&2
    exit 3
fi
mkdir -p "$OUTPUT_DIRECTORY"
cp "$RESULTS_FILE" "${OUTPUT_DIRECTORY}/validation.jsonl"
cp "$SUMMARY_FILE" "${OUTPUT_DIRECTORY}/validation_summary.txt"
