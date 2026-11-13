#!/bin/bash
# DANDI Validations capsule {{ capsule_name }}
#
#   What:      dandi validate, streaming (dandi/dandi-cli#1956), of Dandiset {{ dandiset_id }} ({{ dandiset_version }})
#   Image:     {{ image_reference }}
#              digest {{ image_digest }}
#              ({{ sif_url }} as an Apptainer SIF)
#              (the same SIF, which git-annex downloads: {{ sif_release_url }})
#   Where:     a GitHub Actions runner of https://github.com/{{ organization }}/{{ dandiset_id }},
#              in the capsule's DataLad dataset: branch {{ branch }}
#
# Run from anywhere inside the capsule's dataset. This registers the container with
# datalad-container and executes code/run.sh in it through Apptainer with
# `datalad containers-run`, wrapped in duct to record resource usage. The run commit records the
# command, its inputs (the code, and the Dandiset's manifest in sourcedata/) and its outputs (the
# dataset it mints from the manifest in sourcedata/{{ dandiset_id }}/, with the dandiset.yaml it
# downloads from {{ dandiset_yaml_url }}, and derivatives/). duct's records go to logs/.
#
# A failed run must not leave a partial output behind, since any file under the capsule's
# derivatives/ marks it successful. Its logs are kept either way, which marks it failed.
# On SIGTERM (the job's time limit), the run is stopped and the capsule is likewise left failed.

set -euo pipefail

# The capsule is the root of its own dataset.
CAPSULE="."
DANDISET_ID="{{ dandiset_id }}"
CONTAINER_NAME="{{ container_name }}"
VERSION_CODE="{{ container_version }}"
CONTAINER_DOCKER_URL="{{ docker_url }}"
CONTAINER_SIF_URL="{{ sif_url }}"
# The image goes under envs/, which the capsule annexes (see its .gitattributes).
CONTAINER_IMAGE_PATH="envs/${CONTAINER_NAME}/image"
IMAGE_CACHE="${DANDI_VALIDATIONS_IMAGE_CACHE:-${HOME}/.cache/dandi-validations/images}"
CACHED_SIF="${IMAGE_CACHE}/${CONTAINER_NAME}_${VERSION_CODE}.sif"

cd "$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"

# Runners are ephemeral, so this clone's git-annex must not announce itself in the git-annex branch.
if ! git config --get annex.uuid > /dev/null; then
    git config annex.private true
    git annex init "ephemeral job runner"
fi

# Obtain the image: the SIF minted on GHCR, which is byte-identical for every job, or failing
# that, a conversion of the OCI image.
if [ ! -s "$CACHED_SIF" ]; then
    mkdir -p "$IMAGE_CACHE"
    apptainer pull --force "${CACHED_SIF}.partial" "$CONTAINER_SIF_URL" \
        || apptainer build --force "${CACHED_SIF}.partial" "$CONTAINER_DOCKER_URL"
    mv "${CACHED_SIF}.partial" "$CACHED_SIF"
fi
SIF_SHA256="$(sha256sum "$CACHED_SIF" | cut -d' ' -f1)"

# Register the image with datalad-container. A fresh clone lacks the content of the image it
# recorded last time: when the image is unchanged that content is put back under its key, so no
# commit is made; when it changed, the container is re-registered, which commits the new key.
registered_sha256=""
if git config -f .datalad/config --get "datalad.containers.${CONTAINER_NAME}.image" > /dev/null \
    && [ -L "$CONTAINER_IMAGE_PATH" ]; then
    registered_key="$(git annex lookupkey "$CONTAINER_IMAGE_PATH")"
    registered_sha256="${registered_key##*--}"
    registered_sha256="${registered_sha256%%.*}"
fi
if [ "$registered_sha256" = "$SIF_SHA256" ]; then
    if [ -z "$(git annex find --in=here "$CONTAINER_IMAGE_PATH")" ]; then
        cp "$CACHED_SIF" "${CACHED_SIF}.reinject"
        git annex reinject "${CACHED_SIF}.reinject" "$CONTAINER_IMAGE_PATH"
    fi
else
    update_flag=()
    if git config -f .datalad/config --get "datalad.containers.${CONTAINER_NAME}.image" > /dev/null; then
        update_flag=(--update)
    fi
    datalad containers-add "$CONTAINER_NAME" "${update_flag[@]}" \
        --url "$CACHED_SIF" \
        --image "$CONTAINER_IMAGE_PATH" \
        --call-fmt 'apptainer exec --cleanenv {img} {cmd}'
fi

mkdir -p "${CAPSULE}/logs"
apptainer exec --cleanenv "$CONTAINER_IMAGE_PATH" dandi-validation-versions > "${CAPSULE}/logs/container_versions.json"

# Refuse to run unless the image's installed validators make up the version code it was taken for.
image_version_code="$(dandi-validations version-code --versions "${CAPSULE}/logs/container_versions.json")"
if [ "$image_version_code" != "$VERSION_CODE" ]; then
    echo "The image's validators make up version code ${image_version_code}, not ${VERSION_CODE}." >&2
    exit 1
fi

run_started="$(mktemp)"
stopped=0
# shellcheck disable=SC2329  # Called by the trap below.
stop_run() {
    trap '' TERM INT
    stopped=1
    echo "=== Stopping the run (time limit reached or cancelled) ===" >&2
    run_group="$(pgrep -P "$DUCT_PID" | head -n 1 || true)"
    if [ -n "$run_group" ]; then
        kill -TERM -- "-${run_group}" 2> /dev/null || true
    fi
    for _ in $(seq 60); do
        kill -0 "$DUCT_PID" 2> /dev/null || break
        sleep 1
    done
    if [ -n "$run_group" ]; then
        kill -KILL -- "-${run_group}" 2> /dev/null || true
    fi
}

run_status=0
duct --output-prefix "${CAPSULE}/logs/duct_" --fail-time 0 --message "{{ capsule_name }}: dandi validate ${DANDISET_ID}" \
    datalad containers-run \
        --container-name "$CONTAINER_NAME" \
        --explicit \
        --message "{{ capsule_name }}: dandi validate (streaming) of Dandiset ${DANDISET_ID} ({{ dandiset_version }})" \
        --input "${CAPSULE}/code/run.sh" \
        --input "${CAPSULE}/code/{{ params_file_name }}" \
        --input "${CAPSULE}/sourcedata/assets.jsonld" \
        --output "${CAPSULE}/sourcedata/${DANDISET_ID}" \
        --output "${CAPSULE}/derivatives" \
        "bash '${CAPSULE}/code/run.sh' '${CAPSULE}' '${DANDISET_ID}' '${CAPSULE}/code/{{ params_file_name }}' '{{ dandiset_yaml_url }}'" &
DUCT_PID=$!
trap stop_run TERM INT
wait "$DUCT_PID" || run_status=$?
trap - TERM INT
if [ "$stopped" -eq 1 ]; then
    wait "$DUCT_PID" 2> /dev/null || true
    run_status=124
fi

if [ "$run_status" -ne 0 ]; then
    rm -rf "${CAPSULE}/derivatives"
    # `datalad addurls --nosave` staged what the run minted, which only a completed run commits.
    git reset -q -- "${CAPSULE}/sourcedata/${DANDISET_ID}"
    git clean -q -d -f -- "${CAPSULE}/sourcedata/${DANDISET_ID}"
    # On failure, datalad leaves the run record it would have committed in COMMIT_EDITMSG (in the
    # git directory, which for a worktree is not .git/).
    commit_message="$(git rev-parse --git-dir)/COMMIT_EDITMSG"
    if [ -f "$commit_message" ] && [ "$commit_message" -nt "$run_started" ]; then
        cp "$commit_message" "${CAPSULE}/logs/failed_run_record.txt"
    fi
fi
rm -f "$run_started"
if [ -d "${CAPSULE}/intermediate" ]; then
    chmod -R u+w "${CAPSULE}/intermediate"
    rm -rf "${CAPSULE}/intermediate"
fi

exit "$run_status"
