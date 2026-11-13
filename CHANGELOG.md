# Changelog

## Upcoming

### 🚀 Enhancement

- Added a repository per Dandiset whose own GitHub Actions runner validates it with streaming `dandi validate` in the `dandi-validation` image, and records each run as a job capsule on a branch named by the image's validator version code ([#1](https://github.com/dandi-validations/dandi-validations/pull/1))
- Added the version of dandi-validations, as `-vX.Y.Z`, to the end of each capsule's tag, which names its branch and its `pipeline-dandi+validate_tag-...` directory. Each version of the code now forms its own capsules ([#9](https://github.com/dandi-validations/dandi-validations/pull/9))
- Made a capsule's image downloadable with `datalad get`, so `datalad rerun` works in any clone. Each SIF is also published as a release of this repository, and jobs record its URL in the repository's `git-annex` branch ([#7](https://github.com/dandi-validations/dandi-validations/pull/7))
- Marked each new capsule's `derivatives/validation_summary.txt` as generated, so GitHub collapses it in diffs. `validation.jsonl`, which holds the results themselves, is not marked ([#8](https://github.com/dandi-validations/dandi-validations/pull/8))
- Replaced each capsule's `sourcedata/` link to DANDI's DataLad mirror with the Dandiset as the run validated it. It holds the `assets.jsonld` manifest as downloaded from S3, annexed with the URL of the S3 version it was downloaded from, which `datalad containers-run` records as the run's input. From it, `run.sh` mints the dataset it validates in `sourcedata/{dandiset_id}/`, which the run records as an output: each asset annexed by its SHA-256 key with the URL of its blob on S3, and the Dandiset's `dandiset.yaml`, which the run downloads because `dandi validate` finds the Dandiset's root by it. The `git-annex` branch records each URL once for all of a Dandiset's capsules, so `datalad get` fetches any of them in any clone. Capsules no longer hold `code/assets.tsv` or `code/skipped_assets.tsv`, and their provenance no longer counts skipped assets: an asset that cannot be streamed, such as a Zarr asset, now fails the run. A run is identified by each asset's path, size and SHA-256 only, so changes to metadata, which `dandi validate` does not read, do not form a new one ([#13](https://github.com/dandi-validations/dandi-validations/pull/13))

### 🐛 Bug Fix

- Fixed reruns and queued runs of the Validate workflow failing to push `main` when the commit they checked out predates the capsule's submodule. A job now rebases its clone onto the remote's `main` before forming its capsule ([#6](https://github.com/dandi-validations/dandi-validations/pull/6))
