# DANDI Validations

Running DANDI validation via DataLad-FUSE streaming adapters and collecting their records across this organization's repositories.

Every Dandiset gets its own repository, `https://github.com/dandi-validations/{dandiset_id}`, whose own GitHub Actions
runner validates it with
[`dandi validate --missing-file-content=stream`](https://github.com/dandi/dandi-cli/pull/1956).
Nothing is downloaded: the content-dependent validators (pynwb, nwbinspector) read only what they
need, straight from S3. Each run is recorded in a **capsule** per image, laid out after
[DANDI Compute's job capsules](https://github.com/dandi-compute/001697): a DataLad dataset on its
own branch of the Dandiset's repository, which `main` holds as a submodule.

The validation runs in a `dandi-validation` image that this repository mints on GHCR, tagged by
its **validator version code** following the convention of
[dandi/dandi-archive#2987](https://github.com/dandi/dandi-archive/pull/2987), e.g.
`ghcr.io/dandi-validations/dandi-validation:d0.82.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2`.

This repository holds the container recipe, the orchestration code, the template of the Dandiset
repositories, and the records collected from them (`records/`).

## Dandiset repositories

Each `https://github.com/dandi-validations/{dandiset_id}` is a standalone
([type 2](https://bids-specification.readthedocs.io/en/stable/common-principles.html#storage-of-derived-datasets))
BIDS derivative dataset of its Dandiset:

```
https://github.com/dandi-validations/{dandiset_id}/   (main)
├── dataset_description.json  ← the derivative dataset that collects the capsules
└── pipeline-dandi+validate_tag-{tag}/  ← submodule: branch {tag} of this repository
    ├── dataset_description.json  ← a study dataset: the provenance of its latest run
    ├── code/  ← assets.tsv, dandiset.yaml, name-default.json, run.sh,
    │            summarize.py, submit.sh, submitted_date-... (claim marker)
    ├── logs/  ← duct_info.json, duct_usage.jsonl, duct_stdout, duct_stderr,
    │            container_versions.json, run.json, submit.log
    ├── derivatives/  ← validation.jsonl (sorted by file, then issue), summary.json (plain text)
    ├── envs/dandi-validation/image  ← the image's registration (annexed, with its content on GHCR)
    └── sourcedata/  ← subdataset: https://github.com/dandisets/{dandiset_id} at its draft head
```

- **Capsules on tag branches.** Each capsule is a DataLad dataset on the branch named exactly by
  its image's tag, e.g. `d0.82.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2`. The branch holds `code/`,
  `logs/` and `derivatives/` at its root, the registration of its image
  (`envs/dandi-validation/image`, annexed, its content on GHCR), and the `[DATALAD RUNCMD]` record
  of every run, so `datalad rerun` reproduces any of them. Branches are never merged. A capsule
  holds the latest validation of the Dandiset with its image: a new run (after the Dandiset's files
  change, or a failure) replaces its contents in a new commit, so the branch's history keeps every
  earlier run.
- **Two `dataset_description.json`.** `main`'s describes the repository as a derivative dataset
  collecting the capsules. The template sync manages it. Each capsule's describes the capsule as a
  BIDS study dataset: it holds its raw data under `sourcedata/` and its results under
  `derivatives/`. A job writes it with the provenance of the run:
  - `GeneratedBy` names dandi-cli, the image in BIDS's `Container` (tag and digest), and this code.
  - `SourceDatasets` names the Dandiset and the mirror commit `sourcedata/` pins.
  - `DandiValidations` holds the run's inputs, image and six validator versions.
- **sourcedata/** in each capsule is a subdataset (git submodule) of DANDI's DataLad mirror,
  `https://github.com/dandisets/{dandiset_id}`, pinned to its `draft` head when the run was
  formed. It is there for provenance and self-containment only: it is read with `git ls-remote`
  and never fetched, and the validation reads the Dandiset through the dataset built from the S3
  manifest.
- **main** holds one git submodule per tag branch that has content, pointing at this same
  repository and branch, pinned to the branch's latest commit. If a branch is deleted, the next run
  removes its submodule.
- **Status** is read from what a capsule holds: *pending* (code only), *stalled* (claimed,
  nothing logged), *failed* (logs, no results), *successful* (results under `derivatives/`).
- **One clone, one annex.** On a runner, every capsule branch is a `git worktree` of the single
  clone of the repository, checked out at its submodule's path, so all of them share one object
  store and one git-annex. git-annex is initialized with `annex.private`, and no git-annex branch
  is ever pushed, so ephemeral clones leave no trace.
- **Readable files.** Capsule files are kept in git, uncompressed, so they can be read on GitHub.
  Only the image is annexed. GitHub refuses files over 100 MiB, so a run whose results would
  exceed that records no results and is left *failed*, saying so in its logs.

To get a Dandiset's results:

```bash
git clone https://github.com/dandi-validations/000006
git -C 000006 submodule update --init  # the capsules, not their sourcedata/ (the whole Dandiset)
dandi validate --load 000006/pipeline-dandi+validate_tag-*/derivatives/validation.jsonl --summary
```

## How a job runs

On the runner of `https://github.com/dandi-validations/{dandiset_id}` (its **Validate**
workflow), `dandi-validations run`, from a checkout of `main`:

1. **Forms the capsule.** It reads the Dandiset's manifest from the archive's S3 bucket
   (`dandisets/{id}/draft/assets.jsonld` and `dandiset.yaml`). It checks the branch of the pinned
   image's tag out at the capsule's path as a worktree, or starts it as a new DataLad dataset, and
   links `sourcedata/` to the mirror's current `draft` head. Unless the capsule's
   latest run already succeeded on the same inputs, it writes the capsule's `code/`: the assets
   to validate (`assets.tsv`: path, size, SHA-256, S3 URL), `dandiset.yaml`, the parameters,
   `run.sh` and `submit.sh`. It also resolves the image's digest on GHCR and writes the capsule's
   `dataset_description.json` with its provenance. The inputs are the Dandiset version, the
   image's version code, the parameters and a digest of the assets and `dandiset.yaml`.
2. **Claims it.** It commits the capsule to its branch and pushes the branch, then pins `main`'s
   submodule to it and pushes `main`. A run that never reports back therefore reads as *stalled*.
3. **Runs `code/submit.sh`** inside the capsule's dataset. The script:
   - pulls the `dandi-validation` SIF from GHCR
   - registers it with `datalad containers-add` (`apptainer exec --cleanenv {img} {cmd}`)
   - refuses to go on unless the image's installed validators make up the expected version code
   - executes `code/run.sh` inside the image with `datalad containers-run`, wrapped in
     [duct](https://github.com/con/duct):

   ```bash
   duct --output-prefix "logs/duct_" --fail-time 0 \
       datalad containers-run --container-name dandi-validation --explicit \
           --input "code/..." --output "derivatives" \
           "bash './code/run.sh' ..."
   ```

   Inside the container, `run.sh` follows the recipe at the top of
   [dandi/dandi-cli#1956](https://github.com/dandi/dandi-cli/pull/1956). It builds a DataLad dataset
   of the Dandiset with `datalad addurls --key 'et:SHA256-s{size}--{sha256}'`, so every file gets
   its git-annex key and S3 URL and nothing is fetched. It then runs
   `dandi validate --missing-file-content=stream -J 8 --format json_lines`, and writes its results
   and a summary to `derivatives/`.
4. **Records it.** duct's records, the image's versions, the GitHub Actions run and the output of
   `submit.sh` go to `logs/`. They are committed to the branch, and `main`'s submodule is pinned
   again. Both are pushed.

`dandi validate` exits 1 whenever it reports an error, which is still a completed validation: it
writes `--output` only once every file has been validated, so that file is what marks success. A
run that fails or hits the time limit keeps its logs and leaves no `derivatives/`, which marks the
capsule *failed*.

## The image

`containers/dandi-validation/` follows the image design of dandi/dandi-archive#2987:

- **Environment:** `pixi.toml` lists what the recipe at the top of dandi/dandi-cli#1956 installs
  (git-annex, con-duct, `dandi[datalad]`), plus DataLad and datalad-container. `pixi.lock` pins
  all of it with checksums, including Python, git and git-annex from conda-forge. dandi-cli and
  datalad-fuse are pinned to commits until their streaming support is released.
- **Build:** a two-stage `Dockerfile`. The official pixi image runs `pixi install --locked`, and
  a plain `ubuntu:24.04` receives only the environment, its lock and an activation script. Both
  base images are pinned by digest.
- **Version code:** the tag lists the six validator versions, each behind its one-letter code:
  `d<dandi>-s<dandischema>-n<nwbinspector>-p<pynwb>-h<hdmf>-b<bids-validator-deno>`. Each must
  be a plain `MAJOR.MINOR.PATCH` release, except that a local segment is dropped. For example,
  dandi `0.82.0+28.gbec91fa4`, installed from the #1956 commit, appears as `d0.82.0`. Its full
  version stays in the image's `/opt/dandi-validation/versions.json` and in each job's
  `logs/container_versions.json`. `dandi-validations version-code --lock` computes the code.

The **Build and publish container** workflow mints, for the code that `pixi.lock` makes up:

| Reference | What |
| --- | --- |
| `ghcr.io/dandi-validations/dandi-validation:<version code>` (and `:latest`) | the OCI image, with `pixi.lock` attached (ORAS) |
| `oras://ghcr.io/dandi-validations/dandi-validation:sif-<version code>` | the same image as an Apptainer SIF |

**Immutable images:** an image is never rebuilt, so one code always names one image.
- A code that is already published is skipped if it was built from the same lock, and is an error otherwise.
- The workflow also refuses to publish an image whose installed validators don't make up the code.

`src/dandi_validations/pipeline.json` names the image and version code that jobs run, and a test
checks that it matches `pixi.lock`.

Jobs pull the SIF, so every repository records the same image key. If the SIF is unavailable,
they convert the OCI image, pinned by digest.

To update the image:
1. Change `pixi.toml` and run `pixi lock` in `containers/dandi-validation/`.
2. Set `container.version` to the new code printed by
   `dandi-validations version-code --lock containers/dandi-validation/pixi.lock`.

## Workflows of this repository

| Workflow | What it does |
| --- | --- |
| **Build and publish container** | Builds the image for the version code of `pixi.lock` and pushes the OCI image, its SIF and its lock to GHCR, unless that code is already published. |
| **Make repositories** | Creates `https://github.com/dandi-validations/{dandiset_id}` for every non-empty, open Dandiset (DANDI API, falling back to listing S3) and syncs the template (`src/dandi_validations/repository_template/`) into the `main` of all of them. It can also dispatch their validation. It never touches capsule branches or submodules. |
| **Dispatch validations** | Starts the Validate workflow of every (or the selected) Dandiset repository. |
| **Collect records** | Every 6 hours, reads every capsule of every repository (through `main`'s submodules) into `records/capsules.tsv`, one row per Dandiset and image, described by `records/capsules.json`. |
| **Tests** | The tests, around one example (Dandiset 000006, see [`tests/README.md`](tests/README.md)). What its repository holds after a validation, branch by branch, is committed in `tests/expected_output/`, and one real job runs end to end and is compared with it. |

## Setting up

1. Add the `_GITHUB_API_KEY` organization or repository secret, used only by **Make
   repositories** and **Dispatch validations**: a token that can create repositories in
   `dandi-validations`, push workflow files to them, and dispatch their workflows (a classic PAT
   with `repo` and `workflow`, or a fine-grained token with Administration, Contents, Workflows
   and Actions write access on the organization's repositories). Every other workflow, including
   each Dandiset repository's Validate workflow, uses the built-in `GITHUB_TOKEN`.
2. Run **Build and publish container**, then make the `dandi-validation` package public (Package
   settings → Change visibility), so that every repository's runner can pull it anonymously.
3. Run **Make repositories** (try `dandiset_ids: 000006` first), then **Dispatch validations**.

## Command line

```bash
pip install .
dandi-validations list-dandisets --source s3 --limit 5
dandi-validations version-code --lock containers/dandi-validation/pixi.lock
dandi-validations prepare --dandiset 000006 --path /path/to/dataset   # form a capsule only
dandi-validations run --dandiset 000006 --push origin                 # what the Validate workflow runs
dandi-validations ensure-repos --dandisets 000006 --ref main
dandi-validations dispatch --dandisets 000006
dandi-validations collect --output records
```

To run one job locally end to end (needs Docker and Apptainer):

```bash
bash scripts/e2e_local.sh 000006
```

## Development

Code style, tests, commits and the changelog follow [`AGENTS.md`](AGENTS.md), which adopts the
conventions of [CodyCBakerPhD/historia](https://github.com/CodyCBakerPhD/historia). Its
[pre-commit](https://pre-commit.com) hooks run black, ruff, numpydoc validation, mypy, codespell,
shellcheck and the workflow schema checks:

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```
