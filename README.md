# DANDI Validations

Running DANDI validation via DataLad-FUSE streaming adapters and collecting their records across this organization's repositories.

Every Dandiset gets its own repository, `dandi-validations/{dandiset_id}`, whose own GitHub Actions
runner validates it with
[`dandi validate --missing-file-content=stream`](https://github.com/dandi/dandi-cli/pull/1956).
Nothing is downloaded: the content-dependent validators (pynwb, nwbinspector) read only what they
need, straight from S3. Each run is recorded as a self-contained **job capsule**, laid out like
[DANDI Compute's](https://github.com/dandi-compute/001697).

This repository holds the container recipe, the orchestration code, the template of the Dandiset
repositories, and the records collected from them (`records/`).

## How a job runs

On the runner of `dandi-validations/{dandiset_id}` (its **Validate** workflow), `dandi-validations run`:

1. **Forms the capsule.** It reads the Dandiset's manifest from the archive's S3 bucket
   (`dandisets/{id}/draft/assets.jsonld` and `dandiset.yaml`) and writes the capsule's `code/`:
   the assets to validate (`assets.tsv`: path, size, SHA-256, S3 URL), `dandiset.yaml`, the
   parameters, `run.sh`, and `submit.sh`. The job ID covers the container version, the parameters
   and the digest of those inputs, so an unchanged Dandiset is not validated twice; a failed job is
   retried in a new capsule. The capsule is committed and pushed before it runs (claimed), so a run
   that never reports back reads as *stalled*.
2. **Runs `code/submit.sh`.** It pulls the `dandi-validate` SIF from GHCR, registers it with
   `datalad containers-add` (`apptainer exec --cleanenv {img} {cmd}`), and executes `code/run.sh`
   inside it with `datalad containers-run`, wrapped in [duct](https://github.com/con/duct):

   ```bash
   duct --output-prefix "$CAPSULE/logs/duct_" --fail-time 0 \
       datalad containers-run --container-name dandi-validate --explicit \
           --input "$CAPSULE/code/..." --output "$CAPSULE/derivatives" \
           "bash '$CAPSULE/code/run.sh' ..."
   ```

   Inside the container, `run.sh` follows the recipe at the top of
   [dandi/dandi-cli#1956](https://github.com/dandi/dandi-cli/pull/1956): it builds a DataLad dataset
   of the Dandiset with `datalad addurls --key 'et:SHA256-s{size}--{sha256}'` (every file gets its
   git-annex key and S3 URL, nothing is fetched), then runs
   `dandi validate --missing-file-content=stream -J 8 --format json_lines`. Its results and a
   summary go to the capsule's `derivatives/`, and the `[DATALAD RUNCMD]` commit records the
   command, inputs and outputs, so `datalad rerun` reproduces the job.
3. **Records it.** duct's records, the container's versions, the GitHub Actions run and the output
   of `submit.sh` go to `logs/`; the pipeline's `jobs.tsv` is rebuilt; both are committed and pushed.

`dandi validate` exits 1 whenever it reports an error, which is still a completed validation: it
writes `--output` only once every file has been validated, so that file is what marks success. A
run that fails or hits the time limit keeps its logs and leaves no `derivatives/`, which marks the
capsule *failed*.

## Job capsules

```
dandi-validations/{dandiset_id}/
├── dataset_description.json  ← a BIDS study
└── derivatives/
    └── dandisets-{first 3 digits of the Dandiset ID}/
        └── dandiset-{dandiset_id}/
            └── pipeline-dandi+validate/
                ├── jobs.tsv, jobs.json  ← one row per capsule, rebuilt after each job
                └── job-{YYMMDD}{hash}/
                    ├── code/  ← assets.tsv, dandiset.yaml, name-default.json, run.sh,
                    │            summarize.py, submit.sh, submitted_date-... (claim marker)
                    ├── logs/  ← duct_info.json, duct_usage.jsonl, duct_stdout, duct_stderr,
                    │            container_versions.json, run.json, submit.log
                    ├── derivatives/  ← validation.jsonl (or .jsonl.gz), summary.json
                    └── dataset_description.json  ← provenance (GeneratedBy, DandiValidations)
```

Validation is per Dandiset, so capsules sit directly under the Dandiset rather than under the
`sub-`/`ses-` levels of DANDI Compute's per-asset pipelines; the rest of the nesting is kept so
that the capsules of every repository can be collected into one tree without colliding. Job IDs
follow DANDI Compute's `job-{YYMMDD}{md5[:6]}` scheme, with `-2`, `-3` for re-runs on one day.

Lifecycle status, read from what a capsule holds: *pending* (code only), *stalled* (claimed, nothing
logged), *failed* (logs, no results), *successful* (results under `derivatives/`).

Each Dandiset repository is a DataLad dataset. Capsule files are kept in git (readable on GitHub);
only the container image is annexed, and its content lives on GHCR. Runners initialize git-annex
with `annex.private`, so ephemeral clones never appear in the git-annex branch.

## The container

`containers/dandi-validate/Dockerfile` installs what the PR's recipe installs (`git-annex`,
`con-duct`, `dandi[datalad]` from dandi/dandi-cli#1956, pinned to a commit) plus DataLad.
`src/dandi_validations/pipeline.json` pins the dandi-cli commit and the container version, and is
the single source of truth for both the build and the jobs. The **Build and publish container**
workflow (on changes to either) mints:

| Reference | What |
| --- | --- |
| `ghcr.io/dandi-validations/dandi-validate:<version>` (and `:latest`) | OCI image |
| `oras://ghcr.io/dandi-validations/dandi-validate:sif-<version>` | the same image as an Apptainer SIF |

Jobs pull the SIF, so every repository records the same image key; if it is unavailable they
convert the OCI image instead. Bump `container.version` whenever the image changes: jobs are keyed on it.

## Workflows of this repository

| Workflow | What it does |
| --- | --- |
| **Build and publish container** | Builds the image and pushes the OCI image and SIF to GHCR. |
| **Make repositories** | Creates `dandi-validations/{id}` for every non-empty, open Dandiset (DANDI API, falling back to listing S3) and syncs the template (`src/dandi_validations/repository_template/`) into all of them; optionally dispatches their validation. |
| **Dispatch validations** | Starts the Validate workflow of every (or the selected) Dandiset repository. |
| **Collect records** | Every 6 hours, gathers each repository's `jobs.tsv` into `records/jobs.tsv` and `records/latest.tsv`. |
| **Tests** | Unit tests, and one real job end to end (Dandiset 000006) against a local bare repository. |

## Setting up

1. Add the `_GITHUB_API_KEY` organization or repository secret: a token that can create
   repositories in `dandi-validations`, push workflow files to them, and dispatch their workflows
   (classic PAT with `repo`, `workflow` and `admin:org`, or a fine-grained token with
   Administration, Contents, Workflows and Actions write access).
2. Run **Build and publish container**, then make the `dandi-validate` package public (Package
   settings → Change visibility), so that every repository's runner can pull it anonymously.
3. Run **Make repositories** (try `dandiset_ids: 000006` first), then **Dispatch validations**.

## Command line

```bash
pip install .
dandi-validations list-dandisets --source s3 --limit 5
dandi-validations prepare --dandiset-id 000006 --path /path/to/dataset   # form a capsule only
dandi-validations run --dandiset-id 000006 --push origin                 # what the Validate workflow runs
dandi-validations ensure-repos --dandiset-ids 000006 --codebase-ref main
dandi-validations dispatch --dandiset-ids 000006
dandi-validations collect --output records
```

To run one job locally end to end (needs Docker and Apptainer):

```bash
bash scripts/e2e_local.sh 000006
```
